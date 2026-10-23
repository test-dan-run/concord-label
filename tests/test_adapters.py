"""Adapters: request shapes (httpx MockTransport), parsing, retries, record / replay. No GPU."""

from __future__ import annotations

import base64
import json

import httpx
import numpy as np
import openai
import pytest

from concord_label.adapters import base, whisper_language
from concord_label.adapters.base import AudioRef
from concord_label.adapters.oip_segmenter import OIPSegmenter, parse_segments
from concord_label.adapters.openai_asr import OpenAIASR, parse_transcription
from concord_label.adapters.openai_chat_audio import OpenAIChatAudio, parse_chat_text
from concord_label.core.settings import reset_settings

AUDIO = (np.sin(np.linspace(0, 200, 16000)) * 0.1).astype(np.float32)


def openai_client(handler):
    return openai.OpenAI(base_url="http://test/v1", api_key="x", max_retries=0,
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_openai_asr_word_request_and_parse():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["body"] = req.content
        return httpx.Response(200, json={"text": " Hello there.", "segments": [{"start": 0, "end": 1, "text": "Hello"}],
                                         "words": [{"word": "Hello", "start": 0.0, "end": 0.4},
                                                   {"word": "there.", "start": 0.5, "end": 0.9}]})

    a = OpenAIASR("faster-whisper-large-v3", {"timing": "word", "language": "en"},
                  client=openai_client(handler))
    out, _ = a.run(AUDIO, AudioRef("abc"))
    body = seen["body"].decode(errors="replace")
    assert seen["path"] == "/v1/audio/transcriptions"
    assert 'name="timestamp_granularities[]"' in body and "verbose_json" in body and 'name="language"' in body
    assert out["text"] == "Hello there."
    assert out["words"][1] == {"w": "there.", "start": 0.5, "end": 0.9}


def test_openai_asr_text_only_request():
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode(errors="replace")
        return httpx.Response(200, json={"text": "hi"})

    out, _ = OpenAIASR("whisper-large-v3", {"timing": "text"}, client=openai_client(handler)).run(AUDIO, AudioRef("a"))
    assert "timestamp_granularities" not in seen["body"] and '\r\n\r\njson\r\n' in seen["body"]
    assert out == {"text": "hi", "words": None, "segments": None}


def test_chat_audio_request_has_input_audio():
    seen = {}

    def handler(req):
        seen["json"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "<think>x</think> \"Hi there\""}}]})

    out, _ = OpenAIChatAudio("qwen3-8b", client=openai_client(handler)).run(AUDIO, AudioRef("a"))
    part = seen["json"]["messages"][0]["content"][1]
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "wav"
    assert base64.b64decode(part["input_audio"]["data"])[:4] == b"RIFF"
    assert seen["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert out["text"] == "Hi there"


def test_oip_segmenter_request_and_speaker_names():
    from aicore.oip import OIPClient

    seen = {}

    def handler(req):
        seen["json"] = json.loads(req.content)
        return httpx.Response(200, json={"model_name": "m", "outputs": [
            {"name": "SEGMENTS", "datatype": "FP32", "shape": [2, 2], "data": [2.0, 3.0, 0.5, 1.5]},
            {"name": "SPEAKERS", "datatype": "INT32", "shape": [2], "data": [1, 0]}]})

    sig = {"inputs": [{"name": "AUDIO", "datatype": "FP32", "shape": [-1]},
                      {"name": "SAMPLE_RATE", "datatype": "INT32", "shape": [1]}]}
    c = OIPClient("http://test", "m", sig, http=httpx.Client(base_url="http://test",
                                                             transport=httpx.MockTransport(handler)))
    out, _ = OIPSegmenter("pyannote-diarization", client=c).run(AUDIO, AudioRef("a"))
    names = {t["name"]: t for t in seen["json"]["inputs"]}
    assert names["SAMPLE_RATE"]["data"] == [16000] and names["AUDIO"]["shape"] == [16000]
    assert out == {"segments": [[0.5, 1.5], [2.0, 3.0]], "speakers": ["spk0", "spk1"]}  # sorted by start


def test_parsers_edge_cases():
    assert parse_transcription("plain") == {"text": "plain", "words": None, "segments": None}
    assert parse_chat_text({"choices": []}) == ""
    assert parse_segments({"SEGMENTS": []}) == {"segments": [], "speakers": None}
    with pytest.raises(ValueError):
        parse_segments({"SEGMENTS": [[0, 1]], "SPEAKERS": [0, 1]})
    assert whisper_language("en-SG") == "en" and whisper_language("English") is None


def test_retry_on_503_then_success():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": {"message": "loading", "type": "x", "code": "not_ready"}})
        return httpx.Response(200, json={"text": "ok"})

    a = OpenAIASR("whisper-large-v3", client=openai_client(handler))
    raw, _ = base.invoke(a, AUDIO, AudioRef("a"), sleep=lambda s: None)
    assert raw == {"text": "ok"} and calls["n"] == 3


def test_no_retry_on_400():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "bad", "type": "x", "code": "bad"}})

    a = OpenAIASR("whisper-large-v3", client=openai_client(handler))
    with pytest.raises(openai.BadRequestError):
        base.invoke(a, AUDIO, AudioRef("a"), sleep=lambda s: None)


def test_record_then_replay(tmp_path, monkeypatch, data_dir):
    def handler(req):
        return httpx.Response(200, json={"text": "recorded words"})

    monkeypatch.setenv("RECORD_DIR", str(tmp_path / "rec"))
    reset_settings()
    a = OpenAIASR("whisper-large-v3", {"timing": "text"}, client=openai_client(handler))
    a.run(AUDIO, AudioRef("sha", 1.0, 2.0))
    files = list((tmp_path / "rec" / "whisper-large-v3").glob("*.json"))
    assert len(files) == 1
    monkeypatch.delenv("RECORD_DIR")
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path / "rec"))
    reset_settings()
    b = OpenAIASR("whisper-large-v3", {"timing": "text"}, client=openai_client(lambda r: httpx.Response(500)))
    out, _ = b.run(AUDIO, AudioRef("sha", 1.0, 2.0))
    assert out["text"] == "recorded words"
    with pytest.raises(base.ReplayMiss):
        b.run(AUDIO, AudioRef("other"))


def test_params_hold_no_image_and_hash_like_aicore():
    """The index key both Concord apps compute: adapter params without the image or build, hashed by aicore."""
    from types import SimpleNamespace

    from aicore.catalog import params_hash

    from concord_label.adapters import for_model

    asr = SimpleNamespace(catalog_id="whisper-large-v3", contract="openai", api="audio.transcriptions",
                          timing_level="segment")
    a = for_model(asr, "en-SG")
    assert a.params == {"adapter": "OpenAIASR", "timing": "segment", "language": "en"}
    assert a.params_hash == params_hash(a.params) == base.params_hash(a.params)
    seg = SimpleNamespace(catalog_id="pyannote-diarization", contract="oip-v2", api="infer", timing_level="segment")
    assert for_model(seg, "en").params == {"adapter": "OIPSegmenter"}
    chat = SimpleNamespace(catalog_id="qwen3-8b", contract="openai", api="chat.completions", timing_level="text")
    assert set(for_model(chat, "en").params) == {"adapter", "timing", "prompt"}
    with pytest.raises(base.AdapterError, match="no adapter"):
        for_model(SimpleNamespace(catalog_id="bge-m3", contract="openai", api="embeddings", timing_level=None))


def test_recordings_are_keyed_without_the_image():
    """fixtures/recorded was re-keyed by fixtures/rekey_recordings.py: each file's name is its params' hash."""
    from aicore.catalog import params_hash

    from conftest import RECORDED

    files = sorted(RECORDED.glob("*/*.json"))
    if not files:
        pytest.skip("no recordings")
    for path in files:
        rec = json.loads(path.read_text())
        assert "image" not in rec["params"], path.name
        assert path.stem == AudioRef(rec["audio"]["sha256"], rec["audio"].get("start", 0.0),
                                     rec["audio"].get("end", -1.0)).key(rec["service"], params_hash(rec["params"]))
