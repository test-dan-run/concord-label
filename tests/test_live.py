"""Against the running Model Catalog (`just test-live`): Label lists the models from it, takes a lease the way
a label run does, and calls a model through the catalog's gateway. The catalog starts what the lease names."""

from __future__ import annotations

from pathlib import Path

import pytest

from concord_label.adapters import for_model
from concord_label.adapters.base import AudioRef
from concord_label.datasets.audio import load_derived, sha256_file
from concord_label.platform import catalog

pytestmark = pytest.mark.live

CHECK = Path(__file__).resolve().parents[1] / "fixtures" / "generated" / "check"


@pytest.fixture(scope="module", autouse=True)
def reachable():
    from concord_label.core.settings import get_settings

    get_settings()  # exports AICORE_CATALOG
    if not catalog.get_catalog().available():
        pytest.skip(f"no Model Catalog at {get_settings().catalog_root} (`just catalog-up` in the platform repo)")


def test_catalog_lists_the_speech_models():
    tasks = {r["id"]: catalog.capabilities(r)[0] for r in catalog.models(force=True)}
    assert "vad" in tasks.get("silero-vad", []) and "asr" in tasks.get("faster-whisper-large-v3", [])


def test_adapter_through_the_gateway_under_a_lease():
    rec = catalog.record("faster-whisper-large-v3", force=True)

    class M:
        catalog_id = rec["id"]
        contract, api = rec["contract"], rec["api"]
        timing_level = catalog.capabilities(rec)[1]

    audio = load_derived(CHECK / "check_clip.wav")
    with catalog.leased("label:test-live", [rec["id"]], print, on_wait=print) as lease:
        assert lease.active
        out, latency = for_model(M(), "en").run(audio, AudioRef(sha256_file(CHECK / "check_clip.wav")))
    assert out["words"] and latency > 0
    assert "parcel" in out["text"].lower()
