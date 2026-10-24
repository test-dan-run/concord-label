"""Label against the platform's contracts (PLATFORM_DIR/contracts): every contract Label speaks is in the
platform's registry, the fakes in platform_fakes.py pass the platform's own conformance checks, and Label's
adapters and its text.transform normaliser work against those fakes. No GPU, no running services."""

from __future__ import annotations

import sys

import numpy as np
import openai
import pytest
import yaml
from fastapi.testclient import TestClient

import platform_fakes as fakes
from concord_label.adapters.base import REGISTRY, AudioRef, adapter_class
from concord_label.adapters.oip_segmenter import OIPSegmenter
from concord_label.adapters.openai_asr import OpenAIASR
from concord_label.adapters.openai_chat_audio import OpenAIChatAudio
from concord_label.core.settings import get_settings

# The platform's conformance checks pass a timeout to the client, which FastAPI's TestClient ignores.
pytestmark = pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")

AUDIO = (np.sin(np.linspace(0, 200, 16000)) * 0.1).astype(np.float32)
# (contract, api) pairs Label calls besides its adapters: platform text.transform models as WER normalisers
OTHER_PAIRS = {("openai-inspired", "text.transform")}

FAKES = {
    "asr": ("faster-whisper-large-v3", "openai", "audio.transcriptions", lambda n: fakes.asr_app(n), None),
    "chat": ("qwen3-8b", "openai", "chat.completions", lambda n: fakes.chat_app(n), None),
    "vad": ("silero-vad", "oip-v2", "infer", lambda n: fakes.segmenter_app(n, speakers=False),
            fakes.segmenter_signature(False)),
    "diar": ("pyannote-diarization", "oip-v2", "infer", lambda n: fakes.segmenter_app(n, speakers=True),
             fakes.segmenter_signature(True)),
    "text": ("text-normalizer", "openai-inspired", "text.transform", lambda n: fakes.text_transform_app(n), None),
}


@pytest.fixture(scope="module")
def platform_contracts():
    root = get_settings().platform_dir / "contracts"
    if not (root / "api_types.yaml").is_file():
        pytest.fail(f"the platform's contracts are not at {root}; set PLATFORM_DIR to the platform repository")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def meta(key: str) -> dict:
    name, contract, api, _, sig = FAKES[key]
    m = {"name": name, "contract": contract, "api": api, "health_path": "/health"}
    if sig is not None:
        m["signature"] = sig
    return m


def test_every_contract_label_speaks_is_in_the_platform_registry(platform_contracts):
    adapter_class("openai", "audio.transcriptions")  # imports the adapters, which registers them
    registry = yaml.safe_load((platform_contracts / "api_types.yaml").read_text())
    known = {(contract, api) for contract, apis in registry.items() for api in apis}
    used = set(REGISTRY) | OTHER_PAIRS
    assert used <= known, f"Label speaks contracts the platform does not define: {sorted(used - known)}"
    assert {m[1:3] for m in FAKES.values()} == used, "every contract Label speaks has a fake"


@pytest.mark.parametrize("key", list(FAKES))
def test_fake_passes_the_platform_conformance_checks(platform_contracts, key):
    from conformance import checks_for

    m = meta(key)
    with TestClient(FAKES[key][3](m["name"])) as client:
        for check in checks_for(m):
            check(client, m)


@pytest.fixture
def served_name(monkeypatch):
    """Request bodies carry the service name as the model name (no endpoints map needed)."""
    import aicore

    monkeypatch.setattr(aicore, "model_name", lambda name: name)


def openai_client(key: str) -> openai.OpenAI:
    return openai.OpenAI(base_url="http://testserver/v1", api_key="x", max_retries=0,
                         http_client=TestClient(FAKES[key][3](FAKES[key][0])))


@pytest.mark.parametrize("timing", ["word", "segment", "text"])
def test_asr_adapter_against_the_fake(served_name, timing):
    out, _ = OpenAIASR("faster-whisper-large-v3", {"timing": timing, "language": "en"},
                       client=openai_client("asr")).run(AUDIO, AudioRef("a"))
    assert out["text"] == fakes.TEXT
    assert (out["words"] is not None) == (timing == "word")
    assert (out["segments"] is not None) == (timing in ("word", "segment"))


def test_chat_audio_adapter_against_the_fake(served_name):
    out, _ = OpenAIChatAudio("qwen3-8b", client=openai_client("chat")).run(AUDIO, AudioRef("a"))
    assert out["text"] == fakes.TEXT


@pytest.mark.parametrize("key", ["vad", "diar"])
def test_segmenter_adapter_against_the_fake(key):
    from aicore.oip import OIPClient

    name, _, _, make, sig = FAKES[key]
    client = OIPClient("http://testserver", name, sig, http=TestClient(make(name)))
    out, _ = OIPSegmenter(name, client=client).run(AUDIO, AudioRef("a"))
    assert out["segments"] == sorted(fakes.SEGMENTS)
    assert out["speakers"] == (["spk0", "spk1"] if key == "diar" else None)


def test_platform_normaliser_against_the_fake(monkeypatch):
    import aicore
    from aicore.text import TextServiceClient

    from concord_label.metrics import normalisers

    name = FAKES["text"][0]
    client = TextServiceClient("http://testserver", name, http=TestClient(fakes.text_transform_app(name)))
    monkeypatch.setattr(aicore, "client", lambda service, **kw: client)
    assert normalisers.get(f"platform:{name}").fn(["Hello,   THERE", "two  words"]) == ["hello there", "two words"]
