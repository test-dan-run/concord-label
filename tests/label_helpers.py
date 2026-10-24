"""Helpers shared by the Label tests (fixtures `label_flow` / `labelled` are in conftest.py) and by
tests/e2e_server.py. Everything goes through Label's own HTTP API on recorded responses: models are
registered in the (replay) Model Catalog, the way a person registers them in the catalog's UI, and Label lists
them; datasets are uploaded by local path, and pseudo-labelling fills the output cache."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from concord_label.platform import catalog
from concord_label.runs import jobs

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "generated"
ASR_MODELS = ["whisper-large-v3", "faster-whisper-large-v3"]
SEG_MODELS = ["silero-vad", "pyannote-diarization", "nemotron-3-diarization"]


def _ok(r, *codes):
    assert r.status_code in (codes or (200,)), f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text}"
    return r.json() if r.content else None


def stage_fixture_dataset(dest: Path, refs: str | None = "stm") -> Path:
    """Copy the fixture audio plus one reference format (None: audio only) into a fresh folder."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FIXTURES / "dataset" / "audio", dest / "audio")
    if refs:
        shutil.copytree(FIXTURES / "refs" / refs, dest / "refs")
    return dest


def register_models(client, names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Register each model in the catalog (idempotent; the replay catalog has them already), then read them
    back from Label's model list: capabilities come from the catalog, nothing is typed here."""
    names = names or SEG_MODELS + ASR_MODELS
    cat = catalog.get_catalog()
    for name in names:
        cat.register(name)
    catalog.clear_cache()
    listed = {m["catalog_id"]: m for m in _ok(client.get("/api/models"))["models"]}
    missing = [n for n in names if n not in listed]
    assert not missing, f"not in the catalog: {missing}"
    return {n: listed[n] for n in names}


def create_dataset(client, src: Path, name: str, tags: list[str] | None = None) -> dict[str, Any]:
    ds = _ok(client.post("/api/datasets", json={"name": name, "language": "en", "tags": tags or ["call-centre"],
                                                "local_path": str(src)}), 201)["dataset"]
    jobs.run_pending()
    d = _ok(client.get(f"/api/datasets/{ds['id']}"))
    assert d["status"] == "ready", d
    return d


def audio_only_dataset(client, tmp_path, name="Unlabelled calls") -> int:
    src = stage_fixture_dataset(tmp_path / f"audio-only-{name.replace(' ', '-')}", refs=None)
    d = create_dataset(client, src, name)
    assert d["mode"] == "audio_only"
    return d["id"]


def seed(client, staging: Path, log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Models, an annotated dataset ("Synth fixtures": audio + speaker-attributed STM) and an audio-only
    copy of the same audio ("Unlabelled calls")."""
    out: dict[str, Any] = {"models": register_models(client)}
    out["ids"] = {n: m["catalog_id"] for n, m in out["models"].items()}  # pickers send catalog ids
    log(f"registered {', '.join(out['models'])}")
    out["dataset"] = create_dataset(client, stage_fixture_dataset(staging / "annotated"), "Synth fixtures",
                                    ["synthetic", "en", "call-centre"])
    out["unlabelled"] = audio_only_dataset(client, staging)
    log(f"datasets: annotated {out['dataset']['id']}, audio-only {out['unlabelled']}")
    return out


def label(client, flow, **kw):
    """One pseudo-label run (pyannote -> both Whispers) on the audio-only dataset, run to the end."""
    ids = flow["ids"]
    body = {"tasks": ["asr", "diar"], "seg_model_id": ids["pyannote-diarization"],
            "asr_model_ids": [ids[n] for n in ASR_MODELS], **kw}
    started = _ok(client.post(f"/api/label/datasets/{flow['unlabelled']}/runs", json=body), 202)
    jobs.run_pending()
    job = _ok(client.get(f"/api/jobs/{started['job']['id']}"))
    assert job["status"] == "done", job["error"]
    return started, _ok(client.get(f"/api/label/datasets/{flow['unlabelled']}/results"))
