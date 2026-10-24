"""The Dataset Catalog in Concord Label (DATASET_CATALOG_PLAN.md §8, §12), against aicore's FakeDatasetCatalog:
audio in the store at ingest with holds, Publish to Dataset Catalog, New from Dataset Catalog (with the round trip
of segments and uids), and moving existing originals into the store."""

from __future__ import annotations

import io
import json
import shutil

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select

from concord_label.core import paths
from concord_label.core.models import AudioFile, Dataset, Segment
from concord_label.runs import jobs
from label_helpers import _ok, create_dataset, stage_fixture_dataset


def wav(seconds: float = 1.0, freq: float = 220.0, sr: int = 16000) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    buf = io.BytesIO()
    sf.write(buf, 0.3 * np.sin(2 * np.pi * freq * t), sr, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def annotated(client, tmp_path, name="Synth fixtures") -> dict:
    return create_dataset(client, stage_fixture_dataset(tmp_path / name.replace(" ", "-")), name)


def files_of(session, dataset_id: int) -> list[AudioFile]:
    session.expire_all()
    return list(session.scalars(select(AudioFile).where(AudioFile.dataset_id == dataset_id)
                                .order_by(AudioFile.rel_path)))


def publish(client, dataset_id: int, **kw) -> dict:
    body = {"name": "Synth refs", "scope": "complete", "tags": ["calls"], **kw}
    started = _ok(client.post(f"/api/datasets/{dataset_id}/catalog-publishes", json=body), 202)
    assert started["export"]["destination"] == "catalog"
    jobs.run_pending()
    return _ok(client.get(f"/api/reference-exports/{started['export']['id']}"))


def from_catalog(client, catalog_id: str, n: int, **kw) -> dict:
    made = _ok(client.post("/api/datasets/from-catalog", json={"catalog_dataset_id": catalog_id, "version": n, **kw}),
               201)
    assert made["dataset"]["status"] == "ingesting" and made["job"]["type"] == "catalog-dataset"
    jobs.run_pending()
    d = _ok(client.get(f"/api/datasets/{made['dataset']['id']}"))
    assert d["status"] == "ready", d["ingest_job"]
    return d


def segments(session, dataset_id: int) -> dict[str, list[tuple]]:
    """Per file path: (start, end, speaker, text, uid) of the confirmed segments, times to the millisecond."""
    out: dict[str, list[tuple]] = {}
    for f in files_of(session, dataset_id):
        segs = session.scalars(select(Segment).where(Segment.audio_file_id == f.id, Segment.status == "confirmed"))
        out[f.rel_path] = sorted((round(s.start, 3), round(s.end, 3), s.speaker, s.text, s.uid) for s in segs)
    return out


# ---------------------------------------------------------------- §8.1 audio at upload

def test_config_says_whether_the_dataset_catalog_is_set_up(client, data_dir, monkeypatch):
    assert _ok(client.get("/api/config"))["dataset_catalog"] == {"enabled": False, "store": None, "url": None,
                                                                "ui_url": None}
    assert client.get("/api/dataset-catalog/datasets").status_code == 409
    from concord_label.core.settings import reset_settings

    monkeypatch.setenv("DATASET_STORE", str(data_dir.parent / "store"))
    monkeypatch.setenv("CATALOG_UI_URL", "http://catalog.test:8700/")
    reset_settings()
    cfg = _ok(client.get("/api/config"))["dataset_catalog"]
    assert cfg["enabled"] and cfg["store"] == str(data_dir.parent / "store")
    assert cfg["ui_url"] == "http://catalog.test:8700/datasets"


def test_without_a_store_originals_stay_in_the_data_folder(client, tmp_path, data_dir):
    d = annotated(client, tmp_path)
    assert len(list((data_dir / "originals").iterdir())) == d["files"]
    assert _ok(client.get(f"/api/datasets/{d['id']}"))["catalog_dataset_id"] is None


def test_ingest_sends_originals_to_the_store_and_holds_them(client, session, tmp_path, data_dir, dataset_catalog):
    d = annotated(client, tmp_path)
    assert list((data_dir / "originals").iterdir()) == []  # nothing copied into the data folder
    files = files_of(session, d["id"])
    shas = {f.sha256 for f in files}
    assert dataset_catalog.held() == shas and len(shas) == d["files"] == 7
    for f in files:
        assert paths.original(f.sha256, f.ext) == dataset_catalog.audio_path(f.sha256)
        if f.usable:  # the derived FLAC and peaks stay in data/
            assert paths.derived(f.sha256).is_file() and paths.peaks(f.sha256).is_file()
    f = next(f for f in files if f.ext == ".wav" and f.usable)
    r = client.get(f"/api/audio/{f.id}", params={"original": True})
    assert r.status_code == 200 and r.content == dataset_catalog.audio_path(f.sha256).read_bytes()
    assert r.headers["content-type"].startswith("audio/")


def test_deleting_a_dataset_releases_holds_unless_another_dataset_uses_the_audio(client, session, tmp_path,
                                                                                dataset_catalog):
    a = annotated(client, tmp_path, "Calls A")
    shas = {f.sha256 for f in files_of(session, a["id"])}
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "tone.wav").write_bytes(wav())
    shutil.copy(tmp_path / "Calls-A" / "audio" / "d01.flac", extra / "d01.flac")
    b = create_dataset(client, extra, "Calls B")
    tone = next(f.sha256 for f in files_of(session, b["id"]) if f.rel_path == "tone.wav")
    assert dataset_catalog.held() == shas | {tone}
    assert client.delete(f"/api/datasets/{b['id']}").status_code == 204
    assert dataset_catalog.held() == shas  # d01 is still in Calls A
    assert client.delete(f"/api/datasets/{a['id']}").status_code == 204
    assert dataset_catalog.held() == set()
    assert all(dataset_catalog.audio_path(s).is_file() for s in shas)  # releasing never deletes audio


# ---------------------------------------------------------------- §8.2 publish

def test_first_publish_makes_a_catalog_dataset_and_a_version_without_audio(client, session, tmp_path,
                                                                           dataset_catalog):
    d = annotated(client, tmp_path)
    pv = _ok(client.get(f"/api/datasets/{d['id']}/reference-preview"))
    assert pv["catalog"] == {"dataset_id": None, "name": None, "based_on": None, "latest": None, "stale": False,
                             "deleted": False, "error": None, "url": None}
    rec = publish(client, d["id"])
    assert rec["status"] == "done", rec["error"]
    ds = session.get(Dataset, d["id"])
    session.refresh(ds)
    cid = ds.catalog_dataset_id
    assert cid and ds.catalog_version == 1 and rec["version"] == 1 and rec["catalog_dataset_id"] == cid
    assert rec["catalog_url"] == f"http://127.0.0.1:8700/datasets/{cid}/v/1"
    assert rec["counts"]["created"] is True and rec["counts"]["files"] == 6 and rec["counts"]["uploaded"] == 0
    assert any(j["label"] == "publish to Dataset Catalog · Synth fixtures" for j in _ok(client.get("/api/jobs")))
    cat = dataset_catalog.dataset(cid)
    assert cat["name"] == "Synth refs" and cat["tags"] == ["calls"] and cat["created_by"] == "concord-label"
    v = dataset_catalog.version(cid, 1)
    assert v["parent_n"] is None and v["label_types"] == ["asr", "diar"] and "condition" in v["labels"]
    root = dataset_catalog.version_dir(cid, 1)
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == [
        "files.csv", "manifest.json", "refs/attributes.json", "refs/segments.seglst.json", "refs/uids.jsonl"]
    m = json.loads((root / "manifest.json").read_text())
    assert m["audio_included"] == "none" and m["export_version"] == 1 and m["source"]["dataset_uid"] == cid
    # the history lists the publish; the preview now knows the catalog dataset
    hist = _ok(client.get(f"/api/datasets/{d['id']}/reference-exports"))
    assert [(h["destination"], h["version"]) for h in hist] == [("catalog", 1)]
    pv = _ok(client.get(f"/api/datasets/{d['id']}/reference-preview"))["catalog"]
    assert pv["dataset_id"] == cid and pv["based_on"] == 1 and pv["latest"]["n"] == 1 and not pv["stale"]
    assert pv["latest"]["published_by"] == "concord-label"
    # publishing the same content again makes no new version
    again = publish(client, d["id"])
    assert again["version"] == 1 and again["counts"]["created"] is False
    assert _ok(client.get(f"/api/datasets/{d['id']}"))["catalog_url"].endswith(f"/datasets/{cid}")


def test_publish_uploads_audio_the_store_lacks(client, session, tmp_path, data_dir, monkeypatch):
    """Audio ingested before the store was set up (or brought by Sync) is in data/originals/; a publish uploads it
    first and holds every file."""
    d = annotated(client, tmp_path)
    assert len(list((data_dir / "originals").iterdir())) == 7
    from aicore.datasets import FakeDatasetCatalog

    from concord_label.core.settings import reset_settings
    from concord_label.platform import datasets

    dc = FakeDatasetCatalog(tmp_path / "store", app="concord-label")
    monkeypatch.setenv("DATASET_STORE", str(tmp_path / "store"))
    reset_settings()
    datasets.set_dataset_catalog(dc)
    try:
        rec = publish(client, d["id"])
        assert rec["status"] == "done", rec["error"]
        assert rec["counts"]["uploaded"] == 6  # the six usable files; the corrupt one is not in the version
        assert len([c for c in dc.calls if c[0] == "upload_audio"]) == 6
        assert dc.held() == {f.sha256 for f in files_of(session, d["id"])}
        f = files_of(session, d["id"])[0]
        assert paths.original(f.sha256, f.ext) == dc.audio_path(f.sha256)  # read from the store from now on
    finally:
        datasets.set_dataset_catalog(None)


def test_a_newer_version_from_someone_else_asks_before_publishing(client, session, tmp_path, dataset_catalog):
    d = annotated(client, tmp_path)
    assert publish(client, d["id"])["version"] == 1
    cid = session.get(Dataset, d["id"]).catalog_dataset_id
    # Concord Bench publishes v2 on top of v1
    other = tmp_path / "other"
    shutil.copytree(dataset_catalog.version_dir(cid, 1), other)
    m = json.loads((other / "manifest.json").read_text())
    (other / "manifest.json").write_text(json.dumps({**m, "description": "fixed in Bench"}))
    dataset_catalog.app = "concord-bench"
    try:
        assert dataset_catalog.publish(cid, other, parent=1)["n"] == 2
    finally:
        dataset_catalog.app = "concord-label"
    pv = _ok(client.get(f"/api/datasets/{d['id']}/reference-preview"))["catalog"]
    assert pv["stale"] and pv["based_on"] == 1 and pv["latest"]["n"] == 2
    assert pv["latest"]["published_by"] == "concord-bench" and pv["latest"]["published_at"]
    r = client.post(f"/api/datasets/{d['id']}/catalog-publishes", json={"name": "Synth refs", "tags": ["calls"]})
    assert r.status_code == 409 and "v2 was published by concord-bench" in r.json()["detail"]
    rec = publish(client, d["id"], force=True, scope="any")  # a different scope, so the content differs
    assert rec["status"] == "done", rec["error"]
    assert rec["version"] == 3 and rec["counts"]["parent"] == 1  # the parent still says what it was based on
    assert session.get(Dataset, d["id"]).catalog_version == 3


def test_publish_after_the_catalog_dataset_was_deleted_makes_a_new_one(client, session, tmp_path, dataset_catalog):
    d = annotated(client, tmp_path)
    publish(client, d["id"])
    old = session.get(Dataset, d["id"]).catalog_dataset_id
    dataset_catalog.delete_dataset(old)
    assert _ok(client.get(f"/api/datasets/{d['id']}/reference-preview"))["catalog"]["deleted"] is True
    rec = publish(client, d["id"])
    session.expire_all()
    ds = session.get(Dataset, d["id"])
    assert rec["status"] == "done" and ds.catalog_dataset_id != old and rec["version"] == 1


def test_send_to_bench_still_works_without_the_dataset_catalog(client, tmp_path):
    d = annotated(client, tmp_path)
    assert _ok(client.get(f"/api/datasets/{d['id']}/reference-preview"))["catalog"] is None
    r = client.post(f"/api/datasets/{d['id']}/catalog-publishes", json={"name": "x"})
    assert r.status_code == 409 and "not set up" in r.json()["detail"]
    started = _ok(client.post(f"/api/datasets/{d['id']}/reference-exports", json={"name": "x"}), 202)
    jobs.run_pending()
    assert _ok(client.get(f"/api/reference-exports/{started['export']['id']}"))["status"] == "done"


# ---------------------------------------------------------------- §8.3 new from the Dataset Catalog

def test_a_published_version_opens_in_a_new_dataset_with_the_same_segments_and_uids(client, session, tmp_path,
                                                                                     dataset_catalog):
    """The exit test of D5: publish, then New from Dataset Catalog, gives the same files, segments, uids and file
    labels; the new dataset's next publish is v2 with v1 as its parent."""
    d = annotated(client, tmp_path)
    # a person's edit and a file label, so the round trip carries more than the upload did
    f0 = files_of(session, d["id"])[0]
    seg = session.scalars(select(Segment).where(Segment.audio_file_id == f0.id).order_by(Segment.start)).first()
    seg.text = "an edited transcript"
    f0.meta = {**f0.meta, "noise": "street"}
    session.commit()
    rec = publish(client, d["id"])
    cid = rec["catalog_dataset_id"]

    listed = _ok(client.get("/api/dataset-catalog/datasets"))
    assert [(x["id"], x["latest"]["n"]) for x in listed] == [(cid, 1)]
    detail = _ok(client.get(f"/api/dataset-catalog/datasets/{cid}"))
    assert [v["n"] for v in detail["versions"]] == [1] and detail["versions"][0]["url"].endswith(f"{cid}/v/1")

    new = from_catalog(client, cid, 1)
    assert new["name"] == "Synth refs" and new["tags"] == ["calls"] and new["mode"] == "annotated"
    assert new["label_types"] == ["asr", "diar"] and new["has_speaker_text"]
    assert new["catalog_dataset_id"] == cid and new["catalog_version"] == 1
    assert new["report"]["catalog"]["version"] == 1 and new["report"]["catalog"]["published_by"] == "concord-label"

    published = {f["path"] for f in dataset_catalog.files(cid, 1)}  # the usable files: d07_corrupt stays out
    before = {p: s for p, s in segments(session, d["id"]).items() if p in published}
    assert len(before) == 6
    after = segments(session, new["id"])
    assert after == before
    assert new["report"]["segments_with_uid"] == new["report"]["segments"] == sum(map(len, before.values()))
    old_meta = {f.rel_path: f.meta for f in files_of(session, d["id"]) if f.rel_path in before}
    assert {f.rel_path: f.meta for f in files_of(session, new["id"])} == old_meta
    assert old_meta[f0.rel_path]["noise"] == "street"
    # the audio is the store's, held, and the dataset is a use of the version
    assert all(paths.original(f.sha256, f.ext) == dataset_catalog.audio_path(f.sha256)
               for f in files_of(session, new["id"]))
    assert {f.sha256 for f in files_of(session, new["id"])} <= dataset_catalog.held()
    uses = dataset_catalog.version(cid, 1)["uses"]
    assert [(u["app"], u["ref"]) for u in uses] == [("concord-label", new["uid"])]

    # an edit in the new dataset publishes as v2 on top of v1
    g = files_of(session, new["id"])[0]
    s = session.scalars(select(Segment).where(Segment.audio_file_id == g.id).order_by(Segment.start)).first()
    s.text = "corrected again"
    session.commit()
    rec2 = publish(client, new["id"])
    assert rec2["status"] == "done", rec2["error"]
    assert rec2["catalog_dataset_id"] == cid and rec2["version"] == 2 and rec2["counts"]["parent"] == 1

    # deleting it removes the use and keeps the holds the first dataset still needs
    assert client.delete(f"/api/datasets/{new['id']}").status_code == 204
    assert dataset_catalog.version(cid, 1)["uses"] == []
    assert {f.sha256 for f in files_of(session, d["id"])} == dataset_catalog.held()


def test_a_version_with_audio_only_makes_an_audio_only_dataset(client, session, dataset_catalog):
    ds = dataset_catalog.create_dataset("Raw calls", language="en", tags=["raw"])
    dataset_catalog.add_version(ds["id"], {"a/one.wav": wav(1.0), "b/two.wav": wav(2.0, 330)}, label_types=())
    d = from_catalog(client, ds["id"], 1, name="Raw calls to label")
    assert d["name"] == "Raw calls to label" and d["mode"] == "audio_only" and d["label_types"] == []
    assert d["language"] == "en" and d["tags"] == ["raw"] and d["files"] == 2 and d["usable_files"] == 2
    assert d["ingest_job"]["type"] == "catalog-dataset" and d["ingest_job"]["status"] == "done"
    jobs_list = _ok(client.get("/api/jobs"))
    assert any(j["label"] == "new from Dataset Catalog · v1 · Raw calls to label" for j in jobs_list)
    files = files_of(session, d["id"])
    assert [f.rel_path for f in files] == ["a/one.wav", "b/two.wav"]
    assert [round(f.duration, 2) for f in files] == [1.0, 2.0]
    assert all(paths.derived(f.sha256).is_file() for f in files)
    assert dataset_catalog.held() == {f.sha256 for f in files}


def test_new_from_catalog_reads_a_version_with_text_only_and_matches_uids_by_time(client, session,
                                                                                    dataset_catalog):
    ds = dataset_catalog.create_dataset("Text refs", language="en")
    refs = "".join(json.dumps(r) + "\n" for r in [
        {"file": "one.wav", "start": 0.1, "end": 0.6, "text": "hello there", "attributes": {"accent": "north"}},
        {"file": "one.wav", "start": 0.6, "end": 0.95, "text": "bye", "attributes": {"accent": "north"}},
    ])
    uids = json.dumps({"file": "one.wav", "start": 0.105, "end": 0.6, "uid": "u" * 32}) + "\n"
    attrs = json.dumps({"files": {"one.wav": {"channel": {"kind": "phone"}}}})
    dataset_catalog.add_version(ds["id"], {"one.wav": wav(1.0)},
                                {"segments.jsonl": refs, "uids.jsonl": uids, "attributes.json": attrs})
    d = from_catalog(client, ds["id"], 1)
    assert d["label_types"] == ["asr"] and d["report"]["segments"] == 2 and d["report"]["segments_with_uid"] == 1
    (f,) = files_of(session, d["id"])
    assert f.meta == {"accent": "north", "channel.kind": "phone"}
    segs = segments(session, d["id"])["one.wav"]
    assert [s[:4] for s in segs] == [(0.1, 0.6, None, "hello there"), (0.6, 0.95, None, "bye")]
    assert segs[0][4] == "u" * 32 and segs[1][4] != "u" * 32


def test_new_from_catalog_needs_a_known_version(client, dataset_catalog):
    ds = dataset_catalog.create_dataset("Empty")
    r = client.post("/api/datasets/from-catalog", json={"catalog_dataset_id": ds["id"], "version": 1})
    assert r.status_code == 404 and "no version 1" in r.json()["detail"]


# ---------------------------------------------------------------- §12 move-originals

def test_move_originals_uploads_holds_and_renames_the_folder(client, session, tmp_path, data_dir, monkeypatch):
    from concord_label import cli

    d = annotated(client, tmp_path)
    originals = sorted(p.name for p in (data_dir / "originals").iterdir())
    (data_dir / "originals" / "notes.txt").write_text("not audio")
    assert cli.main(["move-originals"]) == 2  # needs DATASET_STORE

    from aicore.datasets import FakeDatasetCatalog

    from concord_label.core.settings import reset_settings
    from concord_label.platform import datasets

    dc = FakeDatasetCatalog(tmp_path / "store", app="concord-label")
    monkeypatch.setenv("DATASET_STORE", str(tmp_path / "store"))
    reset_settings()
    datasets.set_dataset_catalog(dc)
    try:
        assert cli.main(["move-originals"]) == 0
        moved = data_dir / "originals.moved"
        assert sorted(p.name for p in moved.iterdir()) == sorted([*originals, "notes.txt"])  # nothing deleted
        assert not (data_dir / "originals").exists() or not any((data_dir / "originals").iterdir())
        shas = {f.sha256 for f in files_of(session, d["id"])}
        assert dc.held() == shas and all(dc.audio_path(s).is_file() for s in shas)
        f = files_of(session, d["id"])[0]
        assert paths.original(f.sha256, f.ext) == dc.audio_path(f.sha256)
        # a second run, after Sync brought one more file, adds it to originals.moved/
        (data_dir / "originals").mkdir(exist_ok=True)
        extra = wav(0.5)
        import hashlib

        sha = hashlib.sha256(extra).hexdigest()
        (data_dir / "originals" / f"{sha}.wav").write_bytes(extra)
        assert cli.main(["move-originals"]) == 0
        assert (moved / f"{sha}.wav").is_file() and sha in dc.held()
    finally:
        datasets.set_dataset_catalog(None)


def test_move_originals_keeps_the_folder_when_the_store_is_not_mounted_here(client, tmp_path, data_dir,
                                                                             monkeypatch):
    from aicore.datasets import FakeDatasetCatalog

    from concord_label import cli
    from concord_label.core.settings import reset_settings
    from concord_label.platform import datasets

    annotated(client, tmp_path)
    dc = FakeDatasetCatalog(tmp_path / "elsewhere", app="concord-label")  # not where DATASET_STORE points
    monkeypatch.setenv("DATASET_STORE", str(tmp_path / "store"))
    reset_settings()
    datasets.set_dataset_catalog(dc)
    try:
        assert cli.main(["move-originals"]) == 1
        assert len(list((data_dir / "originals").iterdir())) == 7 and not (data_dir / "originals.moved").exists()
    finally:
        datasets.set_dataset_catalog(None)


@pytest.fixture(autouse=True)
def _no_leftover_catalog():
    yield
    from concord_label.platform import datasets

    datasets.set_dataset_catalog(None)
