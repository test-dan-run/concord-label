"""The round trip between Concord Label and Concord Bench (SPLIT_PLAN.md §10), `just e2e-handoff`.

Both images (`concord-label:0.2.0` from this repository, `concord-bench:0.2.0` from Bench's, each built with
`just build`) run on one user-defined Docker network as `concord-handoff-label` and `concord-handoff-bench`,
in replay mode on fixtures/recorded (Label's copy and Bench's are the same files), each with the other's
address (BENCH_URL, LABEL_URL). The test drives both through their HTTP APIs on random host ports:

1. Bench baseline: the fixture set (fixtures/generated audio + speaker-attributed STM) uploaded to Bench
   directly, one ASR model registered, benchmarked; its WER is the baseline.
2. Label: the same fixture set uploaded as an annotated dataset (its references become confirmed segments).
   A label run (pyannote -> the same ASR model) exercises pseudo-labelling on it; its new segments are
   rejected, so only the uploaded references are confirmed. Replayed responses exist only for the spans the
   recordings were made on (the reference segments and pipeline cuts), which is why the references come
   from the fixture set and not from pseudo-labels.
3. Send to Bench by push: Bench already has every file, so the package carries no audio; staged, accepted,
   benchmarked: the WER equals the baseline.
4. Download: a second package downloaded from Label and uploaded to Bench stages as export version 2 linked
   to version 1; accepted, it is a new dataset and version 1's dataset and score are unchanged.
5. Links: Label's live evaluation shows Bench's locked score for the model (its WER on version 2); a
   segment flagged in Bench and sent back is flagged in Label with the note.

Leases do not apply in replay mode (no platform service starts). Everything is removed afterwards.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.handoff

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BENCH_IMAGE = os.environ.get("HANDOFF_BENCH_IMAGE", "concord-bench:0.2.0")
LABEL_IMAGE = os.environ.get("HANDOFF_LABEL_IMAGE", "concord-label:0.2.0")
NETWORK = "concord-handoff"
BENCH, LABEL = "concord-handoff-bench", "concord-handoff-label"
ASR = "faster-whisper-large-v3"
SEG = "pyannote-diarization"
NOTE = "handoff: the second speaker says more here"


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, text=True, capture_output=True)


def teardown(work: Path | None = None) -> None:
    docker("rm", "-fv", BENCH, LABEL, check=False)  # -v: their anonymous /data volumes
    docker("network", "rm", NETWORK, check=False)
    if work is not None and work.exists():  # nothing is mounted writable, but never leave root-owned files
        docker("run", "--rm", "-v", f"{work}:/w", "alpine:3", "sh", "-c", "rm -rf /w/* /w/.[!.]*", check=False)
        shutil.rmtree(work, ignore_errors=True)


class App:
    """One running app: its HTTP API on a random 127.0.0.1 port of the host."""

    def __init__(self, name: str, port: int):
        host_port = docker("port", name, str(port)).stdout.split()[0].rsplit(":", 1)[1]
        self.name = name
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{host_port}/api", timeout=60)

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
        """A registry entry added as a model: its compatibility check (replayed), then Add model."""
        job = self.job(self.post(f"/registry/{name}/check", 202)["id"])
        return self.post("/models", 201, json={"registry_name": name, "check_job_id": job["id"]})


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


@pytest.fixture(scope="module")
def apps(request, tmp_path_factory):
    if shutil.which("docker") is None or docker("info", check=False).returncode != 0:
        pytest.fail("the round trip needs Docker")
    for ref, repo in ((BENCH_IMAGE, "Concord Bench"), (LABEL_IMAGE, "Concord Label")):
        if docker("image", "inspect", ref, check=False).returncode != 0:
            pytest.fail(f"no image {ref}: run `just build` in the {repo} repository")
    if not (FIXTURES / "recorded" / "endpoints.generated.yaml").is_file():
        pytest.fail("no recorded responses; copy fixtures/recorded from Concord Bench")
    work = tmp_path_factory.mktemp("handoff")
    fixture_set = work / "fixture-set"  # the same references for both apps
    shutil.copytree(FIXTURES / "generated" / "dataset" / "audio", fixture_set / "audio")
    shutil.copytree(FIXTURES / "generated" / "refs" / "stm", fixture_set / "refs")
    teardown()
    docker("network", "create", NETWORK)
    common = ["--network", NETWORK, "-e", "REPLAY_DIR=/handoff/recorded",
              "-e", "AICORE_ENDPOINTS=/handoff/recorded/endpoints.generated.yaml",
              "-v", f"{FIXTURES / 'recorded'}:/handoff/recorded:ro", "-v", f"{fixture_set}:/handoff/fixture-set:ro"]
    try:
        docker("run", "-d", "--name", BENCH, "-p", "127.0.0.1::8600", *common,
               "-e", f"LABEL_URL=http://{LABEL}:8620", BENCH_IMAGE)
        docker("run", "-d", "--name", LABEL, "-p", "127.0.0.1::8620", *common,
               "-e", f"BENCH_URL=http://{BENCH}:8600", LABEL_IMAGE)
        bench, label = App(BENCH, 8600), App(LABEL, 8620)
        for app, peer_url in ((bench, f"http://{LABEL}:8620"), (label, f"http://{BENCH}:8600")):
            cfg = wait_up(app)
            assert cfg["replay"] is True and cfg["contract_version"] == 1
            assert cfg["peer"] == {"url": peer_url, "reachable": True, "contract_version": 1}
        yield bench, label
    finally:
        if request.session.testsfailed:
            for name in (BENCH, LABEL):
                logs = docker("logs", "--tail", "40", name, check=False)
                print(f"\n--- {name} (last lines)\n{logs.stdout}{logs.stderr}")
        teardown(work)


def asr_cell(bench: App, model_id: int, dataset_id: int) -> dict[str, Any]:
    (cell,) = [c for c in bench.get("/benchmarks/asr")["cells"]
               if c["model_id"] == model_id and c["dataset_id"] == dataset_id]
    return cell


def benchmark(bench: App, model_id: int, dataset_id: int) -> dict[str, Any]:
    bench.post("/benchmarks/plan", 202, json={"dataset_id": dataset_id, "seg_model_ids": [],
                                              "asr_model_ids": [model_id], "mode": "stages"})
    bench.idle()
    cell = asr_cell(bench, model_id, dataset_id)
    assert cell["status"] == "done" and cell["value"] is not None, cell
    return cell


def accept(bench: App, import_id: int) -> dict[str, Any]:
    out = bench.post(f"/reference-imports/{import_id}/accept", 202, json={})
    bench.job(out["job"]["id"])
    ds = bench.get(f"/datasets/{out['dataset']['id']}")
    assert ds["status"] == "ready", ds
    assert bench.get(f"/reference-imports/{import_id}")["status"] == "accepted"
    return ds


def send(label: App, dataset_id: int, **kw) -> dict[str, Any]:
    started = label.post(f"/datasets/{dataset_id}/reference-exports", 202,
                         json={"name": "Synth fixtures", "scope": "complete", **kw})
    label.job(started["job"]["id"])
    rec = label.get(f"/reference-exports/{started['export']['id']}")
    assert rec["status"] == "done", rec["error"]
    return rec


def test_round_trip_label_to_bench_and_back(apps):
    bench, label = apps
    tags = ["synthetic", "en", "call-centre"]
    upload = {"name": "Synth fixtures", "language": "en", "tags": tags, "local_path": "/handoff/fixture-set"}

    # 1. Bench baseline: the fixture set uploaded directly, one ASR model, one benchmark
    base_ds = bench.dataset(upload)
    assert base_ds["has_speaker_text"] and base_ds["export_version"] is None
    model = bench.register(ASR)
    baseline = benchmark(bench, model["id"], base_ds["id"])
    print(f"\n1. Bench baseline on the uploaded fixture set: {ASR} WER {baseline['value']!r}")

    # 2. Label: the same fixture set as an annotated dataset; a label run whose new segments are rejected
    lds = label.dataset(upload)
    assert lds["mode"] == "annotated"
    pv = label.get(f"/datasets/{lds['id']}/reference-preview")
    refs = pv["segments"]
    assert refs > 0 and pv["left_out"] == 0 and pv["review"]["confirmed"] == refs, pv
    assert pv["destination"] == "push" and pv["peer"]["reachable"] and pv["files"] == base_ds["usable_files"]
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

    # 3. Send to Bench by push: Bench has every file already, so no audio travels
    v1_rec = send(label, lds["id"])
    assert v1_rec["destination"] == "push" and v1_rec["version"] == 1, v1_rec
    assert v1_rec["audio_included"] == "none" and v1_rec["counts"]["audio_files"] == 0, v1_rec
    imp = bench.get(f"/reference-imports/{v1_rec['bench_import_id']}")
    assert imp["status"] == "staged", imp
    p = imp["preview"]
    assert (p["export_version"], p["new_files"], p["previous_version"]) == (1, 0, None), p
    assert p["files"] == base_ds["usable_files"] and p["segments"] == refs and p["audio_included"] == "none"
    assert p["source"]["app"] == "concord-label" and p["source"]["dataset_uid"] == lds["uid"]
    v1 = accept(bench, imp["id"])
    assert v1["export_version"] == 1 and v1["id"] != base_ds["id"]
    pushed = benchmark(bench, model["id"], v1["id"])
    print(f"3. pushed package v1 -> Bench dataset {v1['id']}: {ASR} WER {pushed['value']!r} "
          f"(baseline {baseline['value']!r})")
    assert pushed["value"] == baseline["value"]

    # 4. Download path: the zip uploaded to Bench by hand is version 2 of the same source
    v2_rec = send(label, lds["id"], destination="download")
    assert v2_rec["destination"] == "download" and v2_rec["version"] == 2 and v2_rec["audio_included"] == "all"
    blob = label.get(v2_rec["download"].removeprefix("/api")).content
    upload_id = bench.post("/uploads")["upload_id"]
    bench.call("PUT", f"/uploads/{upload_id}/chunk", params={"path": v2_rec["file"], "offset": 0}, content=blob)
    imp2 = bench.post("/reference-imports", 201, json={"upload_id": upload_id})
    p2 = imp2["preview"]
    assert imp2["status"] == "staged" and p2["export_version"] == 2 and p2["new_files"] == 0, p2
    assert p2["previous_version"] == {"dataset_id": v1["id"], "name": v1["name"], "export_version": 1}, p2
    assert p2["changes"] == {"files_added": 0, "files_removed": 0, "segments_before": refs, "segments_now": refs}
    before = asr_cell(bench, model["id"], v1["id"])
    v2 = accept(bench, imp2["id"])
    assert v2["id"] not in (v1["id"], base_ds["id"]) and v2["export_version"] == 2
    assert v2["previous_version_id"] == v1["id"]
    assert asr_cell(bench, model["id"], v2["id"])["run_id"] is None  # a new dataset: nothing run on it yet
    after = asr_cell(bench, model["id"], v1["id"])
    assert (after["run_id"], after["value"]) == (before["run_id"], before["value"])  # v1 untouched
    v1_again = bench.get(f"/datasets/{v1['id']}")
    assert (v1_again["files"], v1_again["export_version"]) == (v1["files"], 1)
    newest = benchmark(bench, model["id"], v2["id"])
    assert newest["value"] == baseline["value"]
    print(f"4. downloaded package v2 -> Bench dataset {v2['id']} (previous version {v1['id']}): {ASR} WER "
          f"{newest['value']!r}; v1 still run {after['run_id']} with WER {after['value']!r}")

    # 5. Links: the locked score in Label's live evaluation, flags from Bench back to Label
    live = label.get(f"/label/datasets/{lds['id']}/live")
    locked = {m["service"]: m["locked"] for m in live["asr"]["models"]}
    assert locked[ASR] is not None, live["asr"]["models"]
    assert locked[ASR]["wer"] == newest["value"] and locked[ASR]["export_version"] == 2, locked
    assert locked[ASR]["run_id"] == newest["run_id"]
    utt = bench.get("/errors/asr/utterances", params={"score_id": newest["score_id"], "limit": 1})["rows"][0]
    bench.post(f"/segments/{utt['segment_id']}/flag", json={"flagged": True, "note": NOTE})
    sent = bench.post(f"/datasets/{v2['id']}/flags/send")
    assert sent == {"matched": 1, "unmatched": 0, "sent": 1}, sent
    sha = next(f["sha256"] for f in bench.get(f"/datasets/{v2['id']}/files") if f["id"] == utt["file_id"])
    hit = label.get(f"/files/by-sha/{sha}", params={"dataset_uid": lds["uid"]})
    flagged = [s for s in label.get(f"/label/files/{hit['file_id']}")["segments"] if s["flagged"]]
    assert [(s["start"], s["end"], s["flag_note"], s["status"]) for s in flagged] == \
        [(utt["start"], utt["end"], NOTE, "confirmed")], flagged
    print(f"5. Label shows the locked WER {locked[ASR]['wer']!r} (Bench v2, run {locked[ASR]['run_id']}); the "
          f"segment flagged in Bench at {utt['start']}-{utt['end']} s is flagged in Label with the note")
