"""Reference packages for download (CONTRACTS.md §2), the push to Bench gone (contract version 3), and the links
back from Bench by Dataset Catalog dataset (§1, §3, §4, §5).

Concord Bench is faked with an httpx.MockTransport that answers the contract endpoints with the sample
bodies from contracts/samples/api/."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from concord_label import adapters, peer
from concord_label.core.models import AudioFile, Dataset, Model, ModelOutput, Run, Segment, SegmentStatus
from concord_label.core.settings import CONTRACT_VERSION, reset_settings
from concord_label.label import refpkg
from concord_label.runs import jobs
from label_helpers import _ok

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "contracts" / "samples"
API = SAMPLES / "api"
BENCH = "http://bench.test:8600"


def _checker():
    spec = importlib.util.spec_from_file_location("contracts_check", REPO / "contracts" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECK = _checker()


def sample(name: str) -> dict:
    return json.loads((API / name).read_text())


# ---------------------------------------------------------------- helpers

def send(client, ds: int, **kw) -> dict:
    body = {"name": "Calls refs", "scope": "complete", **kw}
    started = _ok(client.post(f"/api/datasets/{ds}/reference-exports", json=body), 202)
    jobs.run_pending()
    return _ok(client.get(f"/api/reference-exports/{started['export']['id']}"))


def unpack(client, rec: dict, tmp_path: Path) -> Path:
    r = client.get(rec["download"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    return unzip(r.content, tmp_path / f"pkg-{rec['id']}")


def unzip(blob: bytes, dest: Path) -> Path:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        tops = {n.split("/")[0] for n in z.namelist()}
        assert len(tops) == 1, tops  # one top folder: <name>-refs-v<N>/
        z.extractall(dest)
    return dest / tops.pop()


def manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text())


def files_csv(root: Path) -> list[dict]:
    with open(root / "files.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


@pytest.fixture
def no_bench(monkeypatch):
    monkeypatch.delenv("BENCH_URL", raising=False)
    reset_settings()
    peer.use_transport(None)
    yield
    peer.use_transport(None)


class FakeBench:
    """Bench's side of CONTRACTS.md §1 and §4, in memory."""

    def __init__(self):
        self.calls: list[str] = []
        self.scores_status = 200
        self.config = sample("config.bench.json")
        self.scores_query: dict | None = None
        self.down = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=req)
        p = req.url.path
        self.calls.append(f"{req.method} {p}")
        if p == "/api/config":
            return httpx.Response(200, json=self.config)
        if p.startswith("/api/datasets/by-catalog/") and p.endswith("/scores"):
            self.scores_query = {"id": p.split("/")[4], **dict(req.url.params)}
            if self.scores_status != 200:
                return httpx.Response(self.scores_status,
                                      json={"detail": "Concord Bench has no version of that Dataset Catalog dataset"})
            return httpx.Response(200, json=sample("bench.scores-by-catalog.response.json"))
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture
def bench(monkeypatch, data_dir):
    monkeypatch.setenv("BENCH_URL", BENCH)
    reset_settings()
    fake = FakeBench()
    peer.use_transport(httpx.MockTransport(fake))
    yield fake
    peer.use_transport(None)


def review_files(client, ds: int) -> tuple[dict, dict]:
    """A person reviews every segment of the first file (confirm, reject the rejected ones) and confirms
    two segments of the second. Returns (first file view, second file view)."""
    files = [f for f in _ok(client.get(f"/api/label/datasets/{ds}/files")) if f["usable"]]
    views = []
    for k, f in enumerate(files[:2]):
        segs = _ok(client.get(f"/api/label/files/{f['id']}"))["segments"]
        assert len(segs) >= 3
        if k == 0:
            keep = [s["id"] for s in segs if s["status"] != "rejected"]
            drop = [s["id"] for s in segs if s["status"] == "rejected"]
            _ok(client.post("/api/label/segments/bulk", json={"ids": keep, "action": "confirm"}))
            if drop:
                _ok(client.post("/api/label/segments/bulk", json={"ids": drop, "action": "reject"}))
        else:
            _ok(client.post("/api/label/segments/bulk", json={"ids": [s["id"] for s in segs[:2]], "action": "confirm"}))
        views.append(_ok(client.get(f"/api/label/files/{f['id']}")))
    return views[0], views[1]


# ---------------------------------------------------------------- packages (download)

def test_reference_package_of_uploaded_references(label_flow, client, tmp_path, no_bench, session):
    """The annotated dataset: every segment is a confirmed reference with speaker and text -> SegLST."""
    ds = label_flow["dataset"]
    pv = _ok(client.get(f"/api/datasets/{ds['id']}/reference-preview"))
    assert pv["problem"] is None and pv["left_out"] == 0 and pv["catalog"] is None
    assert pv["refs_file"] == "segments.seglst.json" and pv["label_types"] == ["asr", "diar"]
    assert pv["next_version"] == 1 and pv["labels"] == ["condition"]
    rec = send(client, ds["id"], tags=["call-centre", "#en"])
    assert rec["status"] == "done", rec["error"]
    assert rec["destination"] == "download" and rec["version"] == 1
    root = unpack(client, rec, tmp_path)
    assert root.name == "calls-refs-refs-v1"
    assert CHECK.check_package(root) == []
    m = manifest(root)
    usable = [f for f in session.query(AudioFile).filter_by(dataset_id=ds["id"]) if f.usable]
    n_segs = sum(len(f.segments) for f in usable)
    assert m["source"] == {"app": "concord-label", "install_id": _install_id(),
                           "dataset_uid": ds["uid"], "dataset_name": ds["name"]}
    assert m["format_version"] == 2
    assert m["export_version"] == 1 and m["scope"] == "complete" and m["tags"] == ["call-centre", "en"]
    assert m["counts"]["files"] == len(usable) == pv["files"] and m["counts"]["segments"] == n_segs
    assert m["counts"]["hours"] == pytest.approx(sum(f.duration for f in usable) / 3600, abs=1e-5)
    assert m["audio_included"] == "all" and len(list((root / "audio").rglob("*.*"))) == len(usable)
    assert m["normaliser"] == "basic" and m["label_types"] == ["asr", "diar"]
    assert m["review"]["confirmed"] == n_segs and m["review"]["needs_review"] == 0
    rows = files_csv(root)
    assert {r["path"] for r in rows} == {f.rel_path for f in usable}
    assert all(r["sha256"] == f.sha256 and float(r["sample_rate"]) == f.orig_sr
               for r in rows for f in usable if f.rel_path == r["path"])
    seglst = json.loads((root / "refs" / "segments.seglst.json").read_text())
    assert len(seglst) == n_segs and {"session_id", "speaker", "start_time", "end_time", "words"} <= set(seglst[0])
    uids = jsonl(root / "refs" / "uids.jsonl")
    assert len(uids) == n_segs and {u["uid"] for u in uids} == {s.uid for f in usable for s in f.segments}
    assert not (root / "outputs").exists()
    # the files' metadata goes as file labels (CONTRACTS.md §2.7)
    attrs = json.loads((root / "refs" / "attributes.json").read_text())
    assert attrs == {"files": {f.rel_path: f.meta for f in usable}} and "speakers" not in attrs
    assert {lb["condition"] for lb in attrs["files"].values()} == {"clean", "noisy", "meeting", "telephony", "clipped"}
    assert rec["counts"]["labelled_files"] == len(usable)
    # a second package from the same dataset is version 2
    assert send(client, ds["id"], include_audio="never")["version"] == 2
    hist = _ok(client.get(f"/api/datasets/{ds['id']}/reference-exports"))
    assert [h["version"] for h in hist] == [2, 1]
    assert hist[0]["audio_included"] == "none" and hist[0]["counts"]["audio_files"] == 0


def _install_id() -> str:
    from concord_label.label.sync import install_id

    return install_id()


def test_text_only_and_speaker_only_datasets(label_flow, client, tmp_path, no_bench, session):
    ds = label_flow["dataset"]["id"]
    segs = [s for f in session.query(AudioFile).filter_by(dataset_id=ds) if f.usable for s in f.segments]
    for s in segs:
        s.speaker = None
    session.commit()
    rec = send(client, ds)
    root = unpack(client, rec, tmp_path)
    assert CHECK.check_package(root) == []
    assert manifest(root)["label_types"] == ["asr"]
    rows = jsonl(root / "refs" / "segments.jsonl")
    assert len(rows) == len(segs) and set(rows[0]) == {"file", "start", "end", "text"}

    for s in segs:
        s.speaker, s.text = "spk0", None
    session.commit()
    rec = send(client, ds)
    root = unpack(client, rec, tmp_path)
    assert CHECK.check_package(root) == []
    assert manifest(root)["label_types"] == ["diar"] and manifest(root)["export_version"] == 2
    lines = (root / "refs" / "refs.rttm").read_text().splitlines()
    assert len(lines) == len(segs) and lines[0].startswith("SPEAKER ") and lines[0].split()[7] == "spk0"
    # scope any is ASR only: segments without text cannot go
    r = client.post(f"/api/datasets/{ds}/reference-exports", json={"name": "x", "scope": "any"})
    assert r.status_code == 422 and "no transcript" in r.json()["detail"]


def test_pseudo_labelled_dataset_complete_and_any(labelled, client, tmp_path, no_bench, session):
    """Only confirmed segments; scope complete leaves out files that are not fully reviewed."""
    ds = labelled["unlabelled"]
    r = client.post(f"/api/datasets/{ds}/reference-exports", json={"name": "x"})
    assert r.status_code == 422 and "no files qualify" in r.json()["detail"]
    first, second = review_files(client, ds)
    confirmed_1 = [s for s in first["segments"] if s["status"] == "confirmed"]
    confirmed_2 = [s for s in second["segments"] if s["status"] == "confirmed"]
    assert len(confirmed_2) == 2 and any(s["status"] != "confirmed" for s in second["segments"])

    pv = _ok(client.get(f"/api/datasets/{ds}/reference-preview?scope=complete"))
    assert (pv["files"], pv["segments"], pv["left_out"]) == (1, len(confirmed_1), 1)
    assert pv["models"] == ["pyannote-diarization", "whisper-large-v3", "faster-whisper-large-v3"]
    rec = send(client, ds, include_outputs=True)
    assert rec["status"] == "done", rec["error"]
    root = unpack(client, rec, tmp_path)
    assert CHECK.check_package(root) == []
    assert not (root / "refs" / "attributes.json").exists() and pv["labels"] == []  # audio only: no metadata
    m = manifest(root)
    assert m["counts"] == {"files": 1, "segments": len(confirmed_1),
                           "hours": round(first["file"]["duration"] / 3600, 6)}
    assert m["review"]["files_left_out"] == 1 and m["models"] == pv["models"]
    assert m["label_types"] == ["asr", "diar"]
    refs = json.loads((root / "refs" / "segments.seglst.json").read_text())
    assert [(r["start_time"], r["end_time"], r["words"]) for r in refs] == \
        [(s["start"], s["end"], s["text"]) for s in sorted(confirmed_1, key=lambda s: (s["start"], s["end"]))]
    assert {u["uid"] for u in jsonl(root / "refs" / "uids.jsonl")} == {s["uid"] for s in confirmed_1}
    # outputs/seg.jsonl: the label run's pyannote output on that file, params written out in full
    outs = jsonl(root / "outputs" / "seg.jsonl")
    run = session.query(Run).filter_by(dataset_id=ds, status="done").order_by(Run.id.desc()).first()
    assert len(outs) == 1
    o = outs[0]
    assert o["model"] == "pyannote-diarization" and o["sha256"] == files_csv(root)[0]["sha256"]
    # params are exactly the seg adapter's own params for that call, nothing added
    seg_adapter = adapters.for_model(session.get(Model, run.params["seg_model_id"]))
    assert o["params"] == seg_adapter.params == run.params["seg_adapter"]
    assert o["params"] == {"adapter": "OIPSegmenter"}  # no image: the build key travels beside the params
    seg_row = session.query(ModelOutput).filter_by(model_name="pyannote-diarization", audio_sha256=o["sha256"],
                                                   span_end=-1.0).one()
    assert o["build_key"] and o["build_key"] == run.image_tags["pyannote-diarization"]["build_key"]
    assert o["build_key"] == seg_row.build_key and o["image"] == run.image_tags["pyannote-diarization"]["image"]
    assert o["latency_s"] >= 0
    assert o["output"]["segments"] and len(o["output"]["speakers"]) == len(o["output"]["segments"])

    # scope any: both files, confirmed segments only, transcripts only
    rec = send(client, ds, scope="any")
    root = unpack(client, rec, tmp_path)
    assert CHECK.check_package(root) == []
    m = manifest(root)
    assert m["scope"] == "any" and m["label_types"] == ["asr"] and m["export_version"] == 2
    assert m["counts"]["files"] == 2 and m["counts"]["segments"] == len(confirmed_1) + 2
    rows = jsonl(root / "refs" / "segments.jsonl")
    assert {(r["start"], r["end"]) for r in rows if r["file"] == second["file"]["path"]} == \
        {(s["start"], s["end"]) for s in confirmed_2}


# ---------------------------------------------------------------- the push to Bench is gone

def test_push_is_refused_and_earlier_sends_stay_listed(labelled, client, bench, session):
    """Contract version 3: datasets reach Bench through the Dataset Catalog, so Label builds packages for
    download only, even with Bench linked. Sends from before stay in the history."""
    ds = labelled["unlabelled"]
    review_files(client, ds)
    r = client.post(f"/api/datasets/{ds}/reference-exports", json={"name": "x", "scope": "any", "destination": "push"})
    assert r.status_code == 422 and "Dataset Catalog" in r.json()["detail"]
    rec = send(client, ds, scope="any")
    assert rec["status"] == "done" and rec["destination"] == "download" and rec["audio_included"] == "all"
    assert not any(c.startswith(("POST", "PUT")) for c in bench.calls)  # nothing sent to Bench
    old = refpkg.ReferenceExport(dataset_id=ds, name="x", scope="any", tags=[], destination="push", status="done",
                                 version=1, bench_url=BENCH, bench_import_id=4, counts={"files": 2, "segments": 5})
    session.add(old)
    session.commit()
    listed = {h["id"]: h for h in _ok(client.get(f"/api/datasets/{ds}/reference-exports"))}
    assert (listed[old.id]["destination"], listed[old.id]["bench_import_id"]) == ("push", 4)
    # a push queued before the upgrade fails with the reason
    queued = refpkg.ReferenceExport(dataset_id=ds, name="x", scope="any", tags=[], destination="push", status="queued")
    session.add(queued)
    session.flush()
    queued.job_id = jobs.enqueue(session, "reference", {"export_id": queued.id, "dataset_id": ds}).id
    session.commit()
    jobs.run_pending()
    failed = _ok(client.get(f"/api/reference-exports/{queued.id}"))
    assert failed["status"] == "failed" and "contract version 3" in failed["error"]
    assert _ok(client.get(f"/api/jobs/{queued.job_id}"))["label"].startswith("send to Bench")


# ---------------------------------------------------------------- config peer, locked score, by-sha

def test_config_peer_reachable_cached_and_unreachable(client, bench):
    assert _ok(client.get("/api/config"))["peer"] == {"url": BENCH, "reachable": True, "contract_version": CONTRACT_VERSION}
    _ok(client.get("/api/config"))
    assert bench.calls.count("GET /api/config") == 1  # cached
    bench.down = True
    peer.forget()
    assert _ok(client.get("/api/config"))["peer"] == {"url": BENCH, "reachable": False, "contract_version": None}
    bench.down = False
    bench.config = {"app": "something-else"}
    peer.forget()
    assert _ok(client.get("/api/config"))["peer"]["reachable"] is False


CATALOG_ID = "8d4e2b6a1c9f4e3a9b7c5d1e2f3a4b5c"


def in_catalog(session, dataset_id: int, catalog_id: str = CATALOG_ID, version: int = 2) -> None:
    """The dataset as published to the Dataset Catalog (or made from it): Bench finds it by the catalog id."""
    ds = session.get(Dataset, dataset_id)
    ds.catalog_dataset_id, ds.catalog_version = catalog_id, version
    session.commit()


def test_locked_score_from_bench(labelled, client, bench, session):
    ds = labelled["unlabelled"]
    review_files(client, ds)
    # never published to the Dataset Catalog: Bench has no version of it, so it isn't asked
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert all(m["locked"] is None for m in live["asr"]["models"]) and bench.scores_query is None
    in_catalog(session, ds)
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    by = {m["service"]: m["locked"] for m in live["asr"]["models"]}
    assert by["whisper-large-v3"]["wer"] == 0.052 and by["whisper-large-v3"]["run_id"] == 31
    assert by["whisper-large-v3"]["normaliser"] == "basic@1" and by["whisper-large-v3"]["catalog_version"] == 2
    assert by["faster-whisper-large-v3"] is None
    assert bench.scores_query == {"id": CATALOG_ID, "normaliser": "basic@1"}
    assert sum(c.startswith("GET /api/datasets/by-catalog/") for c in bench.calls) == 1  # once per page, not per model
    bench.scores_status = 404
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert all(m["locked"] is None for m in live["asr"]["models"])
    bench.scores_status = 200
    bench.down = True
    peer.forget()
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert all(m["locked"] is None for m in live["asr"]["models"])


def test_locked_score_of_a_model_with_no_outputs_here(label_flow, client, bench, session):
    """An annotated dataset: its references were uploaded, so no model has outputs for them in Label. A model
    Bench has a locked score for is still listed, with that score alone; one it has none for is not."""
    ds = label_flow["dataset"]["id"]
    in_catalog(session, ds)
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert live["asr"]["confirmed"] > 0
    (m,) = live["asr"]["models"]
    assert m["service"] == "whisper-large-v3" and m["locked"]["wer"] == 0.052 and m["locked"]["catalog_version"] == 2
    assert (m["coverage"], m["wer"], m["curve"], m["rank"]) == (0, None, [], None)
    bench.down = True
    peer.forget()
    assert _ok(client.get(f"/api/label/datasets/{ds}/live"))["asr"]["models"] == []


def test_no_bench_no_locked_score(labelled, client, no_bench):
    live = _ok(client.get(f"/api/label/datasets/{labelled['unlabelled']}/live"))
    assert all(m["locked"] is None for m in live["asr"]["models"])


def test_file_by_sha(label_flow, client, session):
    ds = label_flow["dataset"]
    other = session.get(Dataset, label_flow["unlabelled"])  # the same audio, in the audio-only copy
    f = session.query(AudioFile).filter_by(dataset_id=ds["id"]).order_by(AudioFile.rel_path).first()
    in_catalog(session, ds["id"], version=1)
    in_catalog(session, other.id, "e" * 32, version=1)
    assert _ok(client.get(f"/api/files/by-sha/{f.sha256}?catalog_dataset_id={CATALOG_ID}")) == \
        {"file_id": f.id, "dataset_id": ds["id"]}
    # catalog_dataset_id picks the dataset
    hit = _ok(client.get(f"/api/files/by-sha/{f.sha256}?catalog_dataset_id={'e' * 32}"))
    assert hit["dataset_id"] == other.id and hit["file_id"] != f.id
    # two datasets of one catalog dataset: the one based on the higher version is the working dataset
    in_catalog(session, other.id, CATALOG_ID, version=2)
    assert _ok(client.get(f"/api/files/by-sha/{f.sha256}?catalog_dataset_id={CATALOG_ID}"))["dataset_id"] == other.id
    r = client.get(f"/api/files/by-sha/{f.sha256}?catalog_dataset_id={'f' * 32}")
    assert r.status_code == 404 and "Dataset Catalog dataset" in r.json()["detail"]
    assert _ok(client.get(f"/api/files/by-sha/{f.sha256}"))["dataset_id"] in (ds["id"], other.id)
    assert client.get(f"/api/files/by-sha/{'0' * 64}").status_code == 404


# ---------------------------------------------------------------- flags back from Bench

@pytest.fixture
def flagged_source(session, data_dir):
    """The working dataset of the sample flags file's catalog dataset, with the same audio and segment uids."""
    flags = json.loads((SAMPLES / "flags.json").read_text())
    ds = Dataset(name=flags["dataset_name"], status="ready", mode="annotated",
                 catalog_dataset_id=flags["catalog_dataset_id"], catalog_version=flags["catalog_version"])
    session.add(ds)
    session.flush()
    f = AudioFile(dataset_id=ds.id, rel_path="check_clip.wav", sha256=flags["flags"][0]["file_sha256"], ext=".wav",
                  duration=10.56, orig_sr=16000, channels=1)
    session.add(f)
    session.flush()
    uids = [u["uid"] for u in jsonl(SAMPLES / "reference-package-complete" / "refs" / "uids.jsonl")]
    segs = []
    for (s, e, spk), uid in zip([(0.4, 3.4, "agent"), (2.9, 6.81, "caller"), (7.11, 9.96, "agent")], uids,
                                strict=True):
        segs.append(Segment(audio_file_id=f.id, start=s, end=e, speaker=spk, text="x", uid=uid,
                            status=SegmentStatus.confirmed))
    session.add_all(segs)
    session.commit()
    return flags, ds, segs


def test_flags_import_by_uid_and_overlap(client, flagged_source, session):
    flags, ds, segs = flagged_source
    assert CHECK.check_flags(SAMPLES / "flags.json") == []
    out = _ok(client.post("/api/flags/import", json=flags))
    assert {k: out[k] for k in ("matched", "unmatched")} == sample("label.flags-import.response.json")
    assert [s["by"] for s in out["segments"]] == ["uid", "overlap"]
    for s in segs:
        session.refresh(s)
    assert [s.flagged for s in segs] == [False, True, True]
    assert segs[1].flag_note == "'parcel' is 'package' in the audio" and segs[2].flag_note == "speaker should be caller"
    assert segs[1].edited_at is None and segs[1].status == "confirmed"  # a flag is not a review
    view = _ok(client.get(f"/api/label/files/{segs[0].audio_file_id}"))
    assert sum(s["flagged"] for s in view["segments"]) == 2  # the annotator's flags lane


def test_flags_go_to_the_working_dataset_of_the_catalog_dataset(client, flagged_source, session):
    flags, ds, segs = flagged_source
    # an older Label dataset of the same catalog dataset, based on an earlier version, doesn't get them
    older = Dataset(name="older", status="ready", mode="annotated", catalog_dataset_id=flags["catalog_dataset_id"],
                    catalog_version=None)
    session.add(older)
    session.commit()
    assert _ok(client.post("/api/flags/import", json=flags))["dataset_id"] == ds.id


def test_flags_for_another_dataset_are_404(client, flagged_source):
    flags, _, _ = flagged_source
    r = client.post("/api/flags/import", json={**flags, "catalog_dataset_id": "f" * 32})
    assert r.status_code == 404 and "Dataset Catalog dataset" in r.json()["detail"]
    r = client.post("/api/flags/import", json={**flags, "format_version": 9})
    assert r.status_code == 400 and "known: 1, 2" in r.json()["detail"]


def test_flags_format_version_1_still_reads(client, session, data_dir):
    """A flags file from before contract version 3 names the dataset by its uid and this install's id."""
    flags = json.loads((SAMPLES / "flags.json").read_text())
    (data_dir / "install_id").write_text("3f2a9c1e5b7d4a60")
    ds = Dataset(name="Help desk calls", uid="a" * 32, status="ready", mode="annotated")
    session.add(ds)
    session.flush()
    f = AudioFile(dataset_id=ds.id, rel_path="check_clip.wav", sha256=flags["flags"][0]["file_sha256"], ext=".wav",
                  duration=10.56, orig_sr=16000, channels=1)
    session.add(f)
    session.flush()
    session.add(Segment(audio_file_id=f.id, start=2.9, end=6.81, speaker="caller", text="x", uid="u1",
                        status=SegmentStatus.confirmed))
    session.commit()
    src = {"app": "concord-label", "install_id": "3f2a9c1e5b7d4a60", "dataset_uid": ds.uid, "dataset_name": ds.name}
    v1 = {"format": "concord-flags", "format_version": 1, "created_at": flags["created_at"], "source": src,
          "export_version": 1, "flags": flags["flags"][:1]}
    assert _ok(client.post("/api/flags/import", json=v1))["matched"] == 1
    r = client.post("/api/flags/import", json={**v1, "source": {**src, "install_id": "0000000000000000"}})
    assert r.status_code == 404 and "install" in r.json()["detail"]


def test_unmatched_flags_are_counted(client, flagged_source):
    flags, _, _ = flagged_source
    body = {**flags, "flags": [{"uid": None, "file_sha256": "0" * 64, "start": 1, "end": 2, "note": None},
                               {"uid": None, "file_sha256": flags["flags"][0]["file_sha256"], "start": 0.0,
                                "end": 0.3, "note": None}]}
    out = _ok(client.post("/api/flags/import", json=body))
    assert (out["matched"], out["unmatched"]) == (0, 2)
