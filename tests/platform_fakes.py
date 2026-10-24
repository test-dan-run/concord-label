"""Fake model services that follow the platform's contracts (../../platform/contracts/README.md), one per
contract Label speaks. tests/test_platform_contracts.py first runs the platform's own conformance checks
against each fake, then runs Label's adapters against the same fake: when the platform changes a contract,
its conformance checks fail on the fake, and Label's adapters are then tested against the new shape.

The fakes answer with fixed values; they test request and response shapes, not models.
"""

from __future__ import annotations

import math
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

TEXT = "Hello there."
SEGMENTS = [[0.5, 1.5], [0.1, 0.4]]  # deliberately out of order: Label sorts them by start
SPEAKERS = [1, 0]


def _openai_error(status: int, message: str, type_: str, code: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": type_, "code": code}}, status_code=status)


def _openai_base(name: str) -> FastAPI:
    """`/health` and `/v1/models` listing `name`, which every `openai` and `openai-inspired` service serves."""
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": name, "object": "model", "created": 0, "owned_by": "fake"}]}

    return app


def asr_app(name: str) -> FastAPI:
    """`openai` / `audio.transcriptions`: json, verbose_json (with words when asked) and text."""
    app = _openai_base(name)

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> Any:
        form = await request.form()
        if form.get("model") != name:
            return _openai_error(404, f"model {form.get('model')!r} not found", "invalid_request_error", "model_not_found")
        if "file" not in form:
            return _openai_error(400, "file is required", "invalid_request_error", "invalid_request")
        fmt = form.get("response_format") or "json"
        if fmt == "text":
            return PlainTextResponse(TEXT)
        if fmt == "json":
            return {"text": TEXT}
        if fmt != "verbose_json":
            return _openai_error(400, f"unsupported response_format {fmt!r}", "invalid_request_error", "invalid_request")
        body: dict[str, Any] = {"text": TEXT, "language": form.get("language") or "en", "duration": 1.0,
                                "segments": [{"id": 0, "start": 0.0, "end": 0.9, "text": TEXT}]}
        if "word" in form.getlist("timestamp_granularities[]"):
            body["words"] = [{"word": "Hello", "start": 0.0, "end": 0.4}, {"word": "there.", "start": 0.5, "end": 0.9}]
        return body

    return app


def chat_app(name: str) -> FastAPI:
    """`openai` / `chat.completions`: one non-empty assistant message."""
    app = _openai_base(name)

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        body = await request.json()
        if body.get("model") != name:
            return _openai_error(404, f"model {body.get('model')!r} not found", "invalid_request_error", "model_not_found")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            return _openai_error(400, "messages must be a non-empty list", "invalid_request_error", "invalid_request")
        return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": name,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": TEXT}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}

    return app


def segmenter_signature(speakers: bool) -> dict[str, Any]:
    """A VAD (`speakers` False) or diariser signature, as the catalog's verifier recognises a segmenter."""
    outputs = [{"name": "SEGMENTS", "datatype": "FP32", "shape": [-1, 2]}]
    if speakers:
        outputs.append({"name": "SPEAKERS", "datatype": "INT32", "shape": [-1]})
    return {"inputs": [{"name": "AUDIO", "datatype": "FP32", "shape": [-1]},
                       {"name": "SAMPLE_RATE", "datatype": "INT32", "shape": [1]}],
            "outputs": outputs}


def _flat(data: list[Any]) -> list[Any]:
    while data and isinstance(data[0], list):
        data = [x for row in data for x in row]
    return data


def segmenter_app(name: str, speakers: bool) -> FastAPI:
    """`oip-v2` / `infer`: AUDIO + SAMPLE_RATE in, SEGMENTS (and SPEAKERS for a diariser) out."""
    sig = segmenter_signature(speakers)
    app = FastAPI()
    path = f"/v2/models/{name}"

    def error(status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v2/health/live")
    def live() -> dict[str, bool]:
        return {"live": True}

    @app.get("/v2/health/ready")
    def ready() -> dict[str, bool]:
        return {"ready": True}

    @app.get("/v2")
    def server() -> dict[str, Any]:
        return {"name": "fake-oip", "version": "1", "extensions": []}

    @app.get(path)
    def metadata() -> dict[str, Any]:
        return {"name": name, "versions": ["1"], "platform": "fake", **sig}

    @app.get(f"{path}/ready")
    def model_ready() -> dict[str, Any]:
        return {"name": name, "ready": True}

    @app.post(f"{path}/infer")
    async def infer(request: Request) -> Any:
        body = await request.json()
        given = {t.get("name"): t for t in body.get("inputs") or []}
        for spec in sig["inputs"]:
            t = given.get(spec["name"])
            if t is None:
                return error(400, f"missing input {spec['name']}")
            if t.get("datatype") != spec["datatype"]:
                return error(400, f"{spec['name']}: datatype {t.get('datatype')}, want {spec['datatype']}")
            shape = t.get("shape") or []
            if len(shape) != len(spec["shape"]) or any(d not in (-1, a) for d, a in zip(spec["shape"], shape, strict=True)):
                return error(400, f"{spec['name']}: shape {shape} does not fit {spec['shape']}")
            if len(_flat(t.get("data") or [])) != math.prod(shape):
                return error(400, f"{spec['name']}: data count does not match shape {shape}")
        unknown = set(given) - {s["name"] for s in sig["inputs"]}
        if unknown:
            return error(400, f"unknown inputs {sorted(unknown)}")
        outputs = [{"name": "SEGMENTS", "datatype": "FP32", "shape": [len(SEGMENTS), 2], "data": _flat(SEGMENTS)}]
        if speakers:
            outputs.append({"name": "SPEAKERS", "datatype": "INT32", "shape": [len(SPEAKERS)], "data": SPEAKERS})
        out: dict[str, Any] = {"model_name": name, "model_version": "1", "outputs": outputs}
        if "id" in body:
            out["id"] = body["id"]
        return out

    return app


def text_transform_app(name: str) -> FastAPI:
    """`openai-inspired` / `text.transform`: collapses runs of spaces, one output per input."""
    app = _openai_base(name)

    @app.post("/v1/text/transform")
    async def transform(request: Request) -> Any:
        body = await request.json()
        unknown = set(body) - {"model", "input", "options"}
        if unknown:
            return _openai_error(400, f"unknown fields {sorted(unknown)}", "invalid_request_error", "invalid_request")
        if body.get("model") != name:
            return _openai_error(404, f"model {body.get('model')!r} not found", "invalid_request_error", "model_not_found")
        texts = body.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
            return _openai_error(400, "input must be a string or a non-empty list of strings", "invalid_request_error",
                                 "invalid_request")
        if body.get("options"):
            return _openai_error(400, f"unknown options {sorted(body['options'])}", "invalid_request_error",
                                 "invalid_options")
        outputs = [" ".join(t.split()) for t in texts]
        return {"object": "list", "model": name, "created": int(time.time()),
                "data": [{"object": "text.transform", "index": i, "output": o} for i, o in enumerate(outputs)],
                "usage": {"input_chars": sum(map(len, texts)), "output_chars": sum(map(len, outputs))}}

    return app
