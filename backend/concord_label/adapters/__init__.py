"""Model adapters: one per (contract, api), built on `aicore.client(name)`."""

from __future__ import annotations

from typing import Any

from .base import Adapter, AdapterError, AudioRef, ReplayMiss, adapter_class

__all__ = ["Adapter", "AdapterError", "AudioRef", "ReplayMiss", "adapter_class", "for_model"]


def whisper_language(language: str | None) -> str | None:
    """`en-SG` -> `en`; free text such as `English` is not a code, so it is not sent."""
    if not language:
        return None
    code = language.split("-")[0].split("_")[0].lower()
    return code if 2 <= len(code) <= 3 and code.isalpha() else None


def for_model(model: Any, language: str | None = None) -> Adapter:
    """Adapter for a catalog model (a `Model` row: catalog_id, contract, api, timing_level).

    The params (the cache and index key, minus the build) are the adapter class, the timing level for ASR
    and the language for Whisper-style transcription. The image and build are not in them: the catalog's
    build key is kept beside the params (runs/cache.py), so both Concord apps hash the same params."""
    cls = adapter_class(model.contract or "", model.api or "")
    if cls is None:
        raise AdapterError(f"{model.catalog_id}: no adapter for {model.contract} / {model.api}")
    options: dict[str, Any] = {}
    if cls.kind == "asr":
        options["timing"] = model.timing_level or "text"
        if cls.api == "audio.transcriptions" and whisper_language(language):
            options["language"] = whisper_language(language)
    return cls(model.catalog_id, options)
