"""Send to Bench (reference packages, CONTRACTS.md §2 and §4) and the links back from Bench (§1, §3, §5).

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
from concord_label.core.models import AudioFile, Dataset, Model, Run, Segment, SegmentStatus
from concord_label.core.settings import reset_settings
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

    def __init__(self, have: set[str] | None = None):
        self.have = set(have or ())
        self.uploads: dict[str, bytearray] = {}
        self.calls: list[str] = []
        self.import_status = 201
        self.drop_once_at_chunk: int | None = None  # lose the tail of the upload once, at this chunk number
        self.chunks = 0
        self.conflicts = 0
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
        if p == "/api/fingerprints/missing":
            shas = json.loads(req.content)["sha256"]
            return httpx.Response(200, json={"missing": [s for s in shas if s not in self.have]})
        if p == "/api/uploads" and req.method == "POST":
            uid = f"up{len(self.uploads) + 1}"
            self.uploads[uid] = bytearray()
            return httpx.Response(200, json={"upload_id": uid})
        if p.endswith("/chunk"):
            buf = self.uploads[p.split("/")[3]]
            off = int(req.url.params["offset"])
            if off != len(buf):
                self.conflicts += 1
                return httpx.Response(409, json={"detail": f"expected offset {len(buf)}, got {off}"})
            buf.extend(req.content)
            self.chunks += 1
            size = len(buf)
            if self.chunks == self.drop_once_at_chunk:
                del buf[-1000:]  # the end of this chunk got lost after the answer: the next PUT gets a 409
            return httpx.Response(200, json={"path": req.url.params["path"], "size": size})
        if p == "/api/reference-imports":
            if self.import_status == 400:
                return httpx.Response(400, json={"detail": "files.csv lists 1 file Bench has no audio for: x.wav"})
            assert json.loads(req.content)["upload_id"] in self.uploads
            return httpx.Response(201, json=sample("bench.reference-imports.response.json"))
        if p.startswith("/api/datasets/by-source/"):
            self.scores_query = {"uid": p.split("/")[4], **dict(req.url.params)}
            if self.scores_status != 200:
                return httpx.Response(self.scores_status, json={"detail": "Concord Bench has no dataset from that source"})
            return httpx.Response(200, json=sample("bench.scores-by-source.response.json"))
        return httpx.Response(404, json={"detail": "Not Found"})

    def zip_bytes(self) -> bytes:
        (buf,) = self.uploads.values()
        return bytes(buf)


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
    assert pv["destination"] == "download" and pv["problem"] is None and pv["left_out"] == 0
    assert pv["refs_file"] == "segments.seglst.json" and pv["label_types"] == ["asr", "diar"]
    assert pv["next_version"] == 1
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
    assert set(o["params"]) == {"adapter", "image"} and o["params"]["adapter"] == "OIPSegmenter"
    assert o["image"] and o["image"] == o["params"]["image"] and o["latency_s"] >= 0
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


# ---------------------------------------------------------------- push to a fake Bench

def test_push_honours_missing_audio_and_resumes(labelled, client, tmp_path, bench, monkeypatch, session):
    ds = labelled["unlabelled"]
    review_files(client, ds)
    files = [f for f in _ok(client.get(f"/api/label/datasets/{ds}/files")) if f["usable"]][:2]
    shas = {f["id"]: session.get(AudioFile, f["id"]).sha256 for f in files}
    bench.have = {shas[files[0]["id"]]}  # Bench already has the first file
    bench.drop_once_at_chunk = 2
    monkeypatch.setattr(refpkg, "CHUNK_BYTES", 64 * 1024)
    pv = _ok(client.get(f"/api/datasets/{ds}/reference-preview?scope=any"))
    assert pv["destination"] == "push" and pv["peer"] == {"url": BENCH, "reachable": True, "contract_version": 1}
    rec = send(client, ds, scope="any")
    assert rec["status"] == "done", rec["error"]
    assert rec["destination"] == "push" and rec["bench_url"] == BENCH
    assert rec["bench_import_id"] == 4 and rec["bench_preview"]["export_version"] == 1
    assert rec["audio_included"] == "partial" and rec["counts"]["audio_files"] == 1
    assert bench.calls[0] == "GET /api/config"  # the peer check, again in the job before anything is sent
    assert [c for c in bench.calls if c != "GET /api/config"][:2] == ["POST /api/fingerprints/missing",
                                                                      "POST /api/uploads"]
    assert bench.calls[-1] == "POST /api/reference-imports"
    assert bench.chunks > 3 and bench.conflicts == 1  # several chunks; one sent again after the 409
    local = Path(session.get(refpkg.ReferenceExport, rec["id"]).path).read_bytes()
    assert bench.zip_bytes() == local  # the chunks reassemble to the same zip
    root = unzip(bench.zip_bytes(), tmp_path / "pushed")
    assert CHECK.check_package(root) == []
    audio = [p.name for p in (root / "audio").rglob("*.*")]
    assert len(audio) == 1 and audio[0] == Path(files[1]["path"]).name


def test_push_refused_by_bench_marks_the_export_failed(labelled, client, bench):
    ds = labelled["unlabelled"]
    review_files(client, ds)
    bench.import_status = 400
    rec = send(client, ds, scope="any", include_audio="never")
    assert rec["status"] == "failed" and "Bench has no audio for: x.wav" in rec["error"]
    job = _ok(client.get(f"/api/jobs/{rec['job_id']}"))
    assert job["status"] == "failed" and job["label"].startswith("send to Bench")


def test_push_refused_before_upload_when_bench_is_down(labelled, client, bench):
    ds = labelled["unlabelled"]
    review_files(client, ds)
    bench.down = True
    peer.forget()
    r = client.post(f"/api/datasets/{ds}/reference-exports", json={"name": "x", "scope": "any",
                                                                   "destination": "push"})
    assert r.status_code == 409 and "not reachable" in r.json()["detail"]
    # without a destination it falls back to a download
    assert send(client, ds, scope="any")["destination"] == "download"


def test_push_refused_on_another_contract_version(labelled, client, bench):
    bench.config = {**bench.config, "contract_version": 2}
    st = _ok(client.get("/api/config"))["peer"]
    assert st == {"url": BENCH, "reachable": True, "contract_version": 2}
    ds = labelled["unlabelled"]
    review_files(client, ds)
    r = client.post(f"/api/datasets/{ds}/reference-exports", json={"name": "x", "scope": "any",
                                                                   "destination": "push"})
    assert r.status_code == 409 and "contract version 2" in r.json()["detail"]


# ---------------------------------------------------------------- config peer, locked score, by-sha

def test_config_peer_reachable_cached_and_unreachable(client, bench):
    assert _ok(client.get("/api/config"))["peer"] == {"url": BENCH, "reachable": True, "contract_version": 1}
    _ok(client.get("/api/config"))
    assert bench.calls.count("GET /api/config") == 1  # cached
    bench.down = True
    peer.forget()
    assert _ok(client.get("/api/config"))["peer"] == {"url": BENCH, "reachable": False, "contract_version": None}
    bench.down = False
    bench.config = {"app": "something-else"}
    peer.forget()
    assert _ok(client.get("/api/config"))["peer"]["reachable"] is False


def test_locked_score_from_bench(labelled, client, bench, session):
    ds = labelled["unlabelled"]
    review_files(client, ds)
    uid = session.get(Dataset, ds).uid
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    by = {m["service"]: m["locked"] for m in live["asr"]["models"]}
    assert by["whisper-large-v3"]["wer"] == 0.052 and by["whisper-large-v3"]["run_id"] == 31
    assert by["whisper-large-v3"]["normaliser"] == "basic@1" and by["whisper-large-v3"]["export_version"] == 2
    assert by["faster-whisper-large-v3"] is None
    assert bench.scores_query == {"uid": uid, "install_id": _install_id(), "normaliser": "basic@1"}
    assert sum(c.startswith("GET /api/datasets/by-source/") for c in bench.calls) == 1  # once per page, not per model
    bench.scores_status = 404
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert all(m["locked"] is None for m in live["asr"]["models"])
    bench.scores_status = 200
    bench.down = True
    peer.forget()
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert all(m["locked"] is None for m in live["asr"]["models"])


def test_locked_score_of_a_model_with_no_outputs_here(label_flow, client, bench):
    """An annotated dataset: its references were uploaded, so no model has outputs for them in Label. A model
    Bench has a locked score for is still listed, with that score alone; one it has none for is not."""
    ds = label_flow["dataset"]["id"]
    live = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert live["asr"]["confirmed"] > 0
    (m,) = live["asr"]["models"]
    assert m["service"] == "whisper-large-v3" and m["locked"]["wer"] == 0.052 and m["locked"]["export_version"] == 2
    assert (m["coverage"], m["wer"], m["curve"], m["rank"]) == (0, None, [], None)
    bench.down = True
    peer.forget()
    assert _ok(client.get(f"/api/label/datasets/{ds}/live"))["asr"]["models"] == []


def test_no_bench_no_locked_score(labelled, client, no_bench):
    live = _ok(client.get(f"/api/label/datasets/{labelled['unlabelled']}/live"))
    assert all(m["locked"] is None for m in live["asr"]["models"])


def test_file_by_sha(label_flow, client, session):
    ds = label_flow["dataset"]
    f = session.query(AudioFile).filter_by(dataset_id=ds["id"]).order_by(AudioFile.rel_path).first()
    assert _ok(client.get(f"/api/files/by-sha/{f.sha256}?dataset_uid={ds['uid']}")) == \
        {"file_id": f.id, "dataset_id": ds["id"]}
    # the same audio is also in the audio-only copy: dataset_uid picks the dataset
    other = session.get(Dataset, label_flow["unlabelled"])
    hit = _ok(client.get(f"/api/files/by-sha/{f.sha256}?dataset_uid={other.uid}"))
    assert hit["dataset_id"] == other.id and hit["file_id"] != f.id
    assert client.get(f"/api/files/by-sha/{f.sha256}?dataset_uid=nope").status_code == 404
    assert client.get(f"/api/files/by-sha/{'0' * 64}").status_code == 404


# ---------------------------------------------------------------- flags back from Bench

@pytest.fixture
def flagged_source(session, data_dir):
    """A dataset that is the sample flags file's source: same install id, dataset uid and audio."""
    flags = json.loads((SAMPLES / "flags.json").read_text())
    src = flags["source"]
    (data_dir / "install_id").write_text(src["install_id"])
    ds = Dataset(name=src["dataset_name"], uid=src["dataset_uid"], status="ready", mode="annotated")
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


def test_flags_for_another_dataset_or_install_are_404(client, flagged_source):
    flags, _, _ = flagged_source
    other = {**flags, "source": {**flags["source"], "dataset_uid": "f" * 32}}
    r = client.post("/api/flags/import", json=other)
    assert r.status_code == 404 and "no dataset here has uid" in r.json()["detail"]
    other = {**flags, "source": {**flags["source"], "install_id": "0000000000000000"}}
    r = client.post("/api/flags/import", json=other)
    assert r.status_code == 404 and "install" in r.json()["detail"]
    r = client.post("/api/flags/import", json={**flags, "format_version": 9})
    assert r.status_code == 400 and "known: 1" in r.json()["detail"]


def test_unmatched_flags_are_counted(client, flagged_source):
    flags, _, _ = flagged_source
    body = {**flags, "flags": [{"uid": None, "file_sha256": "0" * 64, "start": 1, "end": 2, "note": None},
                               {"uid": None, "file_sha256": flags["flags"][0]["file_sha256"], "start": 0.0,
                                "end": 0.3, "note": None}]}
    out = _ok(client.post("/api/flags/import", json=body))
    assert (out["matched"], out["unmatched"]) == (0, 2)
