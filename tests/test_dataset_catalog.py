"""The Dataset Catalog in Concord Label (DATASET_CATALOG_PLAN.md §8, §12), against aicore's FakeDatasetCatalog:
audio in the store at ingest with holds, Publish to Dataset Catalog, New from Dataset Catalog (with the round trip
of segments and uids), and moving existing originals into the store."""

from __future__ import annotations

import io
import shutil

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import select

from concord_label.core import paths
from concord_label.core.models import AudioFile, Segment
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


@pytest.fixture(autouse=True)
def _no_leftover_catalog():
    yield
    from concord_label.platform import datasets

    datasets.set_dataset_catalog(None)
