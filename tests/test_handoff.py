"""The round trip between Concord Label and Concord Bench through the Dataset Catalog (CONTRACTS.md, contract version
3; DATASET_CATALOG_PLAN.md §10), `just e2e-handoff`.

Three containers run on one user-defined Docker network: the catalog service (`aiplatform-catalog:dataset-catalog`
by default, built from the platform's catalog/Dockerfile, with PLATFORM_DIR mounted read-only) as
`concord-handoff-catalog`, and the two apps (`concord-label` and `concord-bench` images built with `just build`) as
`concord-handoff-label` and `concord-handoff-bench`. A temporary dataset store is mounted read-write in the catalog
and read-only in both apps, at the same path. The apps run in replay mode on fixtures/recorded (Label's copy and
Bench's are the same files), so their Model Catalog is the replay stand-in, while the Dataset Catalog is the real
service. The test drives everything through the HTTP APIs on random host ports:

1. Bench baseline: the fixture set (fixtures/generated audio + speaker-attributed STM) uploaded to Bench
   directly, one ASR model, benchmarked; its WER is the baseline. The originals go to the store.
2. Label: the same fixture set uploaded as an annotated dataset (its references become confirmed segments, and
   the store already has the audio). A label run (pyannote -> the same ASR model) exercises pseudo-labelling on
   it; its new segments are rejected, so only the uploaded references are confirmed. Replayed responses exist
   only for the spans the recordings were made on, which is why the references come from the fixture set.
3. Publish: Label publishes the dataset as version 1 of a new catalog dataset. A push to Bench is refused.
4. Import: Bench imports version 1 from the catalog (no audio travels), benchmarks it, and the WER equals the
   baseline. The files' `condition` arrives as a file label, and the catalog lists Bench's dataset as a use.
5. Versions: Label publishes again with other tags (version 2, parent 1). Bench imports it as a new dataset
   linked to version 1, and version 1's dataset and score are unchanged.
6. Links by catalog dataset id: Label's live evaluation shows Bench's locked score (its WER on version 2); "Open
   in Label" on Bench's error page names the catalog dataset, and Label finds the file by it; a segment flagged in
   Bench and sent back is flagged in Label with the note.

Leases don't apply in replay mode (no model service starts). Everything is removed afterwards. Set
HANDOFF_CATALOG_IMAGE, HANDOFF_BENCH_IMAGE and HANDOFF_LABEL_IMAGE to test other images.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from concord_label.core.settings import CONTRACT_VERSION

pytestmark = pytest.mark.handoff

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
PLATFORM_DIR = Path(os.environ.get("PLATFORM_DIR") or ROOT.parents[1] / "platform").expanduser().resolve()
CATALOG_IMAGE = os.environ.get("HANDOFF_CATALOG_IMAGE", "aiplatform-catalog:dataset-catalog")
BENCH_IMAGE = os.environ.get("HANDOFF_BENCH_IMAGE", "concord-bench:0.2.0")
LABEL_IMAGE = os.environ.get("HANDOFF_LABEL_IMAGE", "concord-label:0.2.0")
NETWORK = "concord-handoff"
CATALOG, BENCH, LABEL = "concord-handoff-catalog", "concord-handoff-bench", "concord-handoff-label"
ASR = "faster-whisper-large-v3"
SEG = "pyannote-diarization"
NOTE = "handoff: the second speaker says more here"
TAGS = ["synthetic", "en", "call-centre"]


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, text=True, capture_output=True)


def teardown(work: Path | None = None) -> None:
    docker("rm", "-fv", CATALOG, BENCH, LABEL, check=False)  # -v: the apps' anonymous /data volumes
    docker("network", "rm", NETWORK, check=False)
    if work is not None and work.exists():  # the catalog runs as this user, but never leave root-owned files
        docker("run", "--rm", "-v", f"{work}:/w", "alpine:3", "sh", "-c", "rm -rf /w/* /w/.[!.]*", check=False)
        shutil.rmtree(work, ignore_errors=True)


def host_url(name: str, port: int) -> str:
    return f"http://127.0.0.1:{docker('port', name, str(port)).stdout.split()[0].rsplit(':', 1)[1]}"


class App:
    """One running container: its HTTP API on a random 127.0.0.1 port of the host."""

    def __init__(self, name: str, port: int):
        self.name = name
        self.http = httpx.Client(base_url=f"{host_url(name, port)}/api", timeout=60)

    def call(self, method: str, path: str, *codes: int, **kw) -> Any:
        r = self.http.request(method, path, **kw)
        assert r.status_code in (codes or (200,)), f"{self.name}: {method} {path} -> {r.status_code}: {r.text[:800]}"
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r

    def get(self, path: str, *codes: int, **kw) -> Any:
        return self.call("GET", path, *codes, **kw)

    def post(self, path: str, *codes: int, **kw) -> Any:
        return self.call("POST", path, *codes, **kw)

    def job(self, job_id: int, timeout: float = 300) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            j = self.get(f"/jobs/{job_id}")
            if j["status"] in ("done", "failed", "blocked"):
                assert j["status"] == "done", f"{self.name}: job {job_id} ({j['label']}) {j['status']}: {j['error']}\n" \
                    + "\n".join(j["log"][-20:])
                return j
            assert time.monotonic() < deadline, f"{self.name}: job {job_id} did not finish in {timeout} s"
            time.sleep(0.5)

    def idle(self, timeout: float = 600) -> None:
        """Wait for every queued or running job."""
        deadline = time.monotonic() + timeout
        while busy := [j for j in self.get("/jobs?limit=200") if j["status"] in ("queued", "running")]:
            assert time.monotonic() < deadline, f"{self.name}: jobs still running: {busy}"
            time.sleep(0.5)
        failed = [j for j in self.get("/jobs?limit=200") if j["status"] in ("failed", "blocked")]
        assert not failed, f"{self.name}: " + "; ".join(f"{j['label']}: {j['error']}" for j in failed)

    def dataset(self, body: dict[str, Any]) -> dict[str, Any]:
        created = self.post("/datasets", 201, json=body)
        self.job(created["job"]["id"])
        ds = self.get(f"/datasets/{created['dataset']['id']}")
        assert ds["status"] == "ready", ds
        return ds

    def register(self, name: str) -> dict[str, Any]:
        """A model from the Model Catalog. In replay mode each app's catalog is the stand-in built from the
        recordings, which has the fixture models registered. `id` is what a run request takes."""
        listed = self.get("/models")
        models = listed["models"] if isinstance(listed, dict) else listed
        m = next(m for m in models if name in (m.get("catalog_id"), m.get("registry_name"), m.get("name")))
        return {**m, "id": m.get("id") or m.get("catalog_id")}


def wait_up(app: App, timeout: float = 120) -> dict[str, Any]:
    """The app answers and reaches its peer (a peer probed before it was up is cached for 30 s)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            cfg = app.http.get("/config").json()
            if cfg["peer"]["reachable"]:
                return cfg
        except (httpx.HTTPError, ValueError):
            pass
        if time.monotonic() > deadline:
            raise AssertionError(f"{app.name} is not up with its peer: {docker('logs', app.name, check=False).stderr[-3000:]}")
        time.sleep(2)


def wait_catalog(catalog: App, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if catalog.http.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise AssertionError(f"the catalog is not up: {docker('logs', CATALOG, check=False).stderr[-3000:]}")
        time.sleep(1)


@pytest.fixture(scope="module")
def apps(request, tmp_path_factory):
    if shutil.which("docker") is None or docker("info", check=False).returncode != 0:
        pytest.fail("the round trip needs Docker")
    for ref, how in ((CATALOG_IMAGE, "`docker build -f catalog/Dockerfile -t aiplatform-catalog:dataset-catalog .` "
                                     "in the platform repository"),
                     (BENCH_IMAGE, "`just build` in the Concord Bench repository"),
                     (LABEL_IMAGE, "`just build` in the Concord Label repository")):
        if docker("image", "inspect", ref, check=False).returncode != 0:
            pytest.fail(f"no image {ref}: run {how}")
    if not (PLATFORM_DIR / "catalog" / "datasets").is_dir():
        pytest.fail(f"PLATFORM_DIR ({PLATFORM_DIR}) is not a platform checkout with the Dataset Catalog")
    if not (FIXTURES / "recorded" / "endpoints.generated.yaml").is_file():
        pytest.fail("no recorded responses; copy fixtures/recorded from Concord Bench")
    work = tmp_path_factory.mktemp("handoff")
    fixture_set = work / "fixture-set"  # the same references for both apps
    shutil.copytree(FIXTURES / "generated" / "dataset" / "audio", fixture_set / "audio")
    shutil.copytree(FIXTURES / "generated" / "refs" / "stm", fixture_set / "refs")
    store = work / "store"  # the Dataset Catalog's store, at the same path in all three containers
    store.mkdir()
    teardown()
    docker("network", "create", NETWORK)
    common = ["--network", NETWORK, "-e", "REPLAY_DIR=/handoff/recorded",
              "-e", "AICORE_ENDPOINTS=/handoff/recorded/endpoints.generated.yaml",
              "-v", f"{FIXTURES / 'recorded'}:/handoff/recorded:ro", "-v", f"{fixture_set}:/handoff/fixture-set:ro",
              "-v", f"{store}:{store}:ro", "-e", f"DATASET_STORE={store}", "-e", f"CATALOG_URL=http://{CATALOG}:8700"]
    try:
        # The catalog service only, without the Docker socket or GPUs: no model service starts here.
        docker("run", "-d", "--name", CATALOG, "--network", NETWORK, "-p", "127.0.0.1::8700",
               "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{PLATFORM_DIR}:/platform:ro", "-v", f"{store}:{store}",
               "-e", f"DATASET_STORE={store}", "-e", "CATALOG_DB=/tmp/catalog/catalog.db",
               "-e", "CATALOG_EXPORT_DIR=/tmp/catalog/exports", "-e", "CATALOG_VERIFY_NEW_BUILDS=0", CATALOG_IMAGE)
        catalog = App(CATALOG, 8700)
        wait_catalog(catalog)
        docker("run", "-d", "--name", BENCH, "-p", "127.0.0.1::8600", *common,
               "-e", f"LABEL_URL=http://{LABEL}:8620", BENCH_IMAGE)
        docker("run", "-d", "--name", LABEL, "-p", "127.0.0.1::8620", *common,
               "-e", f"BENCH_URL=http://{BENCH}:8600", LABEL_IMAGE)
        bench, label = App(BENCH, 8600), App(LABEL, 8620)
        for app, peer_url in ((bench, f"http://{LABEL}:8620"), (label, f"http://{BENCH}:8600")):
            cfg = wait_up(app)
            assert cfg["replay"] is True and cfg["contract_version"] == CONTRACT_VERSION == 3
            assert cfg["peer"] == {"url": peer_url, "reachable": True, "contract_version": CONTRACT_VERSION}
            assert cfg["dataset_catalog"]["enabled"] is True, cfg["dataset_catalog"]
        yield catalog, bench, label
    finally:
        if request.session.testsfailed:
            for name in (CATALOG, BENCH, LABEL):
                logs = docker("logs", "--tail", "40", name, check=False)
                print(f"\n--- {name} (last lines)\n{logs.stdout}{logs.stderr}")
        teardown(work)


def asr_cell(bench: App, catalog_id: str, dataset_id: int) -> dict[str, Any]:
    table = bench.get("/benchmarks/asr")
    model_id = next(m["id"] for m in table["models"] if m["catalog_id"] == catalog_id)  # Bench's own id
    (cell,) = [c for c in table["cells"] if c["model_id"] == model_id and c["dataset_id"] == dataset_id]
    return cell


def benchmark(bench: App, catalog_id: str, dataset_id: int) -> dict[str, Any]:
    """Bench plans take catalog model ids (`asr_models`) since the Model Catalog."""
    bench.post("/benchmarks/plan", 202, json={"dataset_id": dataset_id, "asr_models": [catalog_id], "mode": "stages"})
    bench.idle()
    cell = asr_cell(bench, catalog_id, dataset_id)
    assert cell["status"] == "done" and cell["value"] is not None, cell
    return cell


def publish(label: App, dataset_id: int, **kw) -> dict[str, Any]:
    """Publish to Dataset Catalog from Label: a `reference` job; the record names the catalog dataset and version."""
    started = label.post(f"/datasets/{dataset_id}/catalog-publishes", 202,
                         json={"name": "Synth fixtures", "scope": "complete", "tags": TAGS, **kw})
    label.job(started["job"]["id"])
    rec = label.get(f"/reference-exports/{started['export']['id']}")
    assert rec["status"] == "done" and rec["destination"] == "catalog", rec["error"]
    return rec


def import_version(bench: App, catalog_id: str, n: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bench's "From Dataset Catalog": the version staged with its preview, then accepted into a new dataset."""
    imp = bench.post("/dataset-catalog/imports", 201, json={"dataset_id": catalog_id, "n": n})
    assert imp["status"] == "staged", imp
    out = bench.post(f"/reference-imports/{imp['id']}/accept", 202, json={})
    bench.job(out["job"]["id"])
    ds = bench.get(f"/datasets/{out['dataset']['id']}")
    assert ds["status"] == "ready", ds
    assert bench.get(f"/reference-imports/{imp['id']}")["status"] == "accepted"
    return imp["preview"], ds


def test_round_trip_label_to_bench_and_back_through_the_dataset_catalog(apps):
    catalog, bench, label = apps
    upload = {"name": "Synth fixtures", "language": "en", "tags": TAGS, "local_path": "/handoff/fixture-set"}

    # 1. Bench baseline: the fixture set uploaded directly, one ASR model, one benchmark
    base_ds = bench.dataset(upload)
    assert base_ds["has_speaker_text"] and base_ds["export_version"] is None and base_ds["catalog"] is None
    model = bench.register(ASR)
    baseline = benchmark(bench, model["id"], base_ds["id"])
    stats = catalog.get("/store/stats")
    assert stats["audio"]["files"] >= base_ds["usable_files"], stats  # Bench's originals are in the store
    assert "concord-bench" in {h["app"] for h in stats["holds"]}, stats
    print(f"\n1. Bench baseline on the uploaded fixture set: {ASR} WER {baseline['value']!r}; "
          f"{stats['audio']['files']} files in the store")

    # 2. Label: the same fixture set as an annotated dataset; a label run whose new segments are rejected
    lds = label.dataset(upload)
    assert lds["mode"] == "annotated"
    assert catalog.get("/store/stats")["audio"]["files"] == stats["audio"]["files"]  # the same audio, stored once
    pv = label.get(f"/datasets/{lds['id']}/reference-preview")
    refs = pv["segments"]
    assert refs > 0 and pv["left_out"] == 0 and pv["review"]["confirmed"] == refs, pv
    assert pv["catalog"]["dataset_id"] is None and pv["files"] == base_ds["usable_files"]
    ids = {n: label.register(n)["id"] for n in (SEG, ASR)}
    started = label.post(f"/label/datasets/{lds['id']}/runs", 202, json={
        "tasks": ["asr", "diar"], "seg_model_id": ids[SEG], "asr_model_ids": [ids[ASR]]})
    label.job(started["job"]["id"])
    summary = label.get(f"/label/datasets/{lds['id']}/results")["summary"]
    assert summary["kept_reviewed"] > 0, summary  # pseudo-labels never replace the confirmed references
    machine = [s["id"] for f in label.get(f"/label/datasets/{lds['id']}/files") if f["usable"]
               for s in label.get(f"/label/files/{f['id']}")["segments"] if s["status"] != "confirmed"]
    if machine:
        label.post("/label/segments/bulk", json={"ids": machine, "action": "reject"})
    pv = label.get(f"/datasets/{lds['id']}/reference-preview")
    assert (pv["segments"], pv["left_out"], pv["review"]["confirmed"]) == (refs, 0, refs), pv
    print(f"2. Label: {refs} uploaded references confirmed; the label run wrote {summary['segments']} new "
          f"segments ({summary['kept_reviewed']} cuts kept the references), all rejected")

    # 3. Publish version 1 to the Dataset Catalog; the push to Bench is gone (contract version 3)
    r = label.post(f"/datasets/{lds['id']}/reference-exports", 422,
                   json={"name": "Synth fixtures", "scope": "complete", "destination": "push"})
    assert "Dataset Catalog" in r["detail"]
    v1_rec = publish(label, lds["id"])
    cid = v1_rec["catalog_dataset_id"]
    assert v1_rec["version"] == 1 and len(cid) == 32, v1_rec
    assert label.get(f"/datasets/{lds['id']}")["catalog_dataset_id"] == cid
    (cv1,) = catalog.get(f"/datasets/{cid}")["versions"]
    assert (cv1["n"], cv1["published_by"], cv1["counts"]["files"]) == (1, "concord-label", base_ds["usable_files"])
    assert cv1["counts"]["segments"] == refs and cv1["manifest"]["source"]["dataset_uid"] == cid
    print(f"3. Label published v1 of catalog dataset {cid} ({cv1['counts']['files']} files, {refs} segments)")

    # 4. Bench imports version 1 from the catalog: no audio travels, the same WER as the baseline
    assert cid in [d["id"] for d in bench.get("/dataset-catalog/datasets")]
    p, v1 = import_version(bench, cid, 1)
    assert (p["export_version"], p["new_files"], p["previous_version"]) == (1, 0, None), p
    assert p["files"] == base_ds["usable_files"] and p["segments"] == refs and p["audio_included"] == "none"
    assert p["source"] == {"app": "concord-label", "install_id": "dataset-catalog", "dataset_uid": cid,
                           "dataset_name": "Synth fixtures"}, p["source"]
    assert p["labels"] == ["condition"], p  # the files' metadata, as file labels (CONTRACTS.md §2.7)
    assert v1["export_version"] == 1 and v1["id"] != base_ds["id"]
    assert (v1["catalog"]["dataset_id"], v1["catalog"]["n"], v1["catalog"]["published"]) == (cid, 1, False)
    base_cond, v1_cond = ({lb["key"]: lb for lb in d["report"]["labels"]}["condition"] for d in (base_ds, v1))
    counts = ({v["value"]: v["n"] for v in c["values"]} for c in (v1_cond, base_cond))
    assert v1_cond["level"] == "file" and next(counts).items() <= next(counts).items(), (v1_cond, base_cond)
    uses = catalog.get(f"/datasets/{cid}/versions/1")["uses"]
    assert [(u["app"], u["ref"]) for u in uses] == [("concord-bench", str(v1["id"]))], uses
    imported = benchmark(bench, model["id"], v1["id"])
    print(f"4. Bench imported v1 as dataset {v1['id']}: {ASR} WER {imported['value']!r} "
          f"(baseline {baseline['value']!r})")
    assert imported["value"] == baseline["value"]

    # 5. A second publish (other tags) is version 2 with parent 1; Bench links it to version 1's dataset
    v2_rec = publish(label, lds["id"], tags=[*TAGS, "reviewed"])
    assert v2_rec["catalog_dataset_id"] == cid and v2_rec["version"] == 2 and v2_rec["counts"]["parent"] == 1
    p2, v2 = import_version(bench, cid, 2)
    assert p2["export_version"] == 2 and p2["new_files"] == 0, p2
    assert p2["previous_version"] == {"dataset_id": v1["id"], "name": v1["name"], "export_version": 1}, p2
    assert p2["changes"] == {"files_added": 0, "files_removed": 0, "segments_before": refs, "segments_now": refs}
    before = asr_cell(bench, model["id"], v1["id"])
    assert v2["id"] not in (v1["id"], base_ds["id"]) and v2["previous_version_id"] == v1["id"]
    assert (v2["catalog"]["dataset_id"], v2["catalog"]["n"]) == (cid, 2) and "reviewed" in v2["tags"]
    assert asr_cell(bench, model["id"], v2["id"])["run_id"] is None  # a new dataset: nothing run on it yet
    after = asr_cell(bench, model["id"], v1["id"])
    assert (after["run_id"], after["value"]) == (before["run_id"], before["value"])  # v1 untouched
    newest = benchmark(bench, model["id"], v2["id"])
    assert newest["value"] == baseline["value"]
    print(f"5. Label published v2; Bench imported it as dataset {v2['id']} (previous version {v1['id']}): {ASR} WER "
          f"{newest['value']!r}; v1 still run {after['run_id']} with WER {after['value']!r}")

    # 6. Links by catalog dataset id: the locked score, Open in Label, flags back to Label
    live = label.get(f"/label/datasets/{lds['id']}/live")
    locked = {m["service"]: m["locked"] for m in live["asr"]["models"]}
    assert locked[ASR] is not None, live["asr"]["models"]
    assert locked[ASR]["wer"] == newest["value"] and locked[ASR]["catalog_version"] == 2, locked
    assert locked[ASR]["run_id"] == newest["run_id"] and locked[ASR]["dataset_id"] == v2["id"]
    utt = bench.get("/errors/asr/utterances", params={"score_id": newest["score_id"], "limit": 1})["rows"][0]
    link = urlsplit(bench.get(f"/errors/asr/utterances/{utt['id']}")["label_url"])
    sha = next(f["sha256"] for f in bench.get(f"/datasets/{v2['id']}/files") if f["id"] == utt["file_id"])
    assert f"{link.scheme}://{link.netloc}" == f"http://{LABEL}:8620" and link.path == f"/label/file/by-sha/{sha}"
    query = parse_qs(link.query)
    assert query["catalog_dataset_id"] == [cid] and float(query["t"][0]) == pytest.approx(utt["start"], abs=1e-3)
    hit = label.get(f"/files/by-sha/{sha}", params={"catalog_dataset_id": cid})
    assert hit["dataset_id"] == lds["id"], hit
    bench.post(f"/segments/{utt['segment_id']}/flag", json={"flagged": True, "note": NOTE})
    flags = bench.get(f"/datasets/{v2['id']}/flags.json")
    assert (flags["format_version"], flags["catalog_dataset_id"], flags["catalog_version"]) == (2, cid, 2), flags
    sent = bench.post(f"/datasets/{v2['id']}/flags/send")
    assert sent == {"matched": 1, "unmatched": 0, "sent": 1}, sent
    flagged = [s for s in label.get(f"/label/files/{hit['file_id']}")["segments"] if s["flagged"]]
    assert [(s["start"], s["end"], s["flag_note"], s["status"]) for s in flagged] == \
        [(utt["start"], utt["end"], NOTE, "confirmed")], flagged
    print(f"6. Label shows the locked WER {locked[ASR]['wer']!r} (Bench v2, run {locked[ASR]['run_id']}); Open in "
          f"Label names catalog dataset {cid}; the segment flagged in Bench at {utt['start']}-{utt['end']} s is "
          "flagged in Label with the note")
