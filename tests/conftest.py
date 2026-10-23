"""Shared fixtures. Every test gets its own CONCORD_DATA and database; nothing needs the platform or the model
catalog: every test runs against an in-memory `FakeCatalog` (aicore.catalog), and the replay fixtures against
the replay catalog built from fixtures/recorded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "fixtures" / "generated"
RECORDED = ROOT / "fixtures" / "recorded"


def truth() -> dict:
    return json.loads((GEN / "dataset" / "truth.json").read_text())


def dialogue_files() -> dict:
    """The six usable dialogues (no corrupt / long file)."""
    return {k: v for k, v in truth().items() if k.startswith("d0") and k != "d07_corrupt"}


@pytest.fixture(autouse=True)
def fake_catalog(request):
    """An empty in-memory catalog for every test, so nothing reaches a real one (live tests use the real one).
    Tests add models with `fake_catalog.add_model(...)` or swap in their own with `catalog.set_catalog`."""
    from aicore.catalog import FakeCatalog

    from concord_label.platform import catalog

    if request.node.get_closest_marker("live"):
        yield None
        return
    cat = FakeCatalog([])
    catalog.set_catalog(cat)
    yield cat
    catalog.set_catalog(None)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from concord_label.core import db
    from concord_label.core.settings import reset_settings

    monkeypatch.setenv("CONCORD_DATA", str(tmp_path / "data"))
    for var in ("REPLAY_DIR", "RECORD_DIR", "AICORE_CATALOG", "CATALOG_URL", "CATALOG_UI_URL", "PUBLIC_URL",
                "CONCORD_PUBLIC_URL"):
        monkeypatch.delenv(var, raising=False)
    reset_settings()
    db.reset_engines()
    db.migrate()
    yield tmp_path / "data"
    db.reset_engines()
    reset_settings()


@pytest.fixture
def session(data_dir):
    from concord_label.core.db import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client(data_dir):
    from fastapi.testclient import TestClient

    from concord_label.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def replay_catalog():
    """A fresh replay catalog (the fixture models, verified and running) as the catalog of this test."""
    from concord_label.platform import catalog

    cat = catalog.replay_catalog.__wrapped__(str(RECORDED), "")
    catalog.set_catalog(cat)
    return cat


@pytest.fixture
def replay(monkeypatch, data_dir, replay_catalog):
    """Recorded responses instead of live services, with the endpoint map they were recorded with, and the
    replay catalog standing in for the model catalog."""
    from concord_label.core.settings import reset_settings

    if not (RECORDED / "endpoints.generated.yaml").is_file():
        pytest.skip("no recorded responses; copy fixtures/recorded from Concord Bench")
    monkeypatch.setenv("REPLAY_DIR", str(RECORDED))
    monkeypatch.setenv("AICORE_ENDPOINTS", str(RECORDED / "endpoints.generated.yaml"))
    reset_settings()
    yield RECORDED
    reset_settings()


# ---------------------------------------------------------------- Label (Phase 2)

@pytest.fixture
def label_flow(replay, client, tmp_path):
    """Catalog models, an annotated dataset and an audio-only copy of the same audio ("unlabelled")."""
    from label_helpers import seed

    return seed(client, tmp_path / "staging")


@pytest.fixture
def labelled(label_flow, client):
    """The label flow plus one pseudo-label run (pyannote -> both Whispers) on the audio-only dataset."""
    from label_helpers import label

    started, res = label(client, label_flow)
    label_flow["started"], label_flow["results"] = started, res
    return label_flow
