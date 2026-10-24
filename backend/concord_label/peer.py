"""Concord Bench, the peer app (SPLIT_PLAN.md §5, CONTRACTS.md §1 and §4).

Everything Label asks of Bench goes through here: whether it answers (`status`, cached for
`STATUS_TTL_S` so pages never wait on it twice), and the locked scores the live evaluation shows. Label
calls Bench only when `BENCH_URL` is set, Bench answers `/api/config` as `concord-bench`, and its contract
version is ours (`usable`). Datasets reach Bench through the Dataset Catalog (contract version 3), so Label
pushes nothing to Bench.

Tests swap the HTTP transport with `use_transport` (an `httpx.MockTransport` or an ASGI app).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from .core.settings import CONTRACT_VERSION, get_settings

STATUS_TIMEOUT_S = 1.0
STATUS_TTL_S = 30.0
SCORES_TIMEOUT_S = 2.0

_transport: httpx.BaseTransport | None = None
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


class PeerError(Exception):
    """Bench refused a request (its `detail`) or could not be reached."""


def use_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests: send every request to Bench through `transport` (None: the network)."""
    global _transport
    _transport = transport
    forget()


def forget() -> None:
    with _lock:
        _cache.clear()


def bench_url() -> str | None:
    url = (get_settings().bench_url or "").strip().rstrip("/")
    return url or None


def client(timeout: float) -> httpx.Client:
    url = bench_url()
    if not url:
        raise PeerError("BENCH_URL is not set")
    return httpx.Client(base_url=url, timeout=timeout, transport=_transport)


def _probe(url: str) -> dict[str, Any]:
    out: dict[str, Any] = {"url": url, "reachable": False, "contract_version": None}
    try:
        with client(STATUS_TIMEOUT_S) as c:
            r = c.get("/api/config")
        body = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return out
    if isinstance(body, dict) and body.get("app") == "concord-bench":
        out["reachable"] = True
        cv = body.get("contract_version")
        out["contract_version"] = cv if isinstance(cv, int) else None
    return out


def status(force: bool = False) -> dict[str, Any]:
    """`/api/config`'s `peer`: {"url", "reachable", "contract_version"}."""
    url = bench_url()
    if not url:
        return {"url": None, "reachable": False, "contract_version": None}
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
        if hit and not force and now - hit[0] < STATUS_TTL_S:
            return dict(hit[1])
    out = _probe(url)
    with _lock:
        _cache[url] = (time.monotonic(), out)
    return dict(out)


def mark_unreachable() -> None:
    """A call to Bench timed out or failed: treat Bench as down until the cache expires."""
    url = bench_url()
    if url:
        with _lock:
            _cache[url] = (time.monotonic(), {"url": url, "reachable": False, "contract_version": None})


def usable(st: dict[str, Any] | None = None) -> bool:
    st = st if st is not None else status()
    return bool(st["reachable"]) and st["contract_version"] == CONTRACT_VERSION


def refusal(st: dict[str, Any]) -> str | None:
    """Why Label must not call Bench now (None when it may)."""
    if not st["url"]:
        return "BENCH_URL is not set"
    if not st["reachable"]:
        return f"Concord Bench at {st['url']} is not reachable"
    if st["contract_version"] != CONTRACT_VERSION:
        return (f"Concord Bench at {st['url']} speaks contract version {st['contract_version']}; "
                f"this Concord Label knows version {CONTRACT_VERSION}")
    return None


def detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except (ValueError, AttributeError):
        d = None
    if d is None:
        d = r.text[:500] or r.reason_phrase
    return d if isinstance(d, str) else str(d)


# ---------------------------------------------------------------- locked scores (CONTRACTS.md §4)

def locked_scores(catalog_dataset_id: str, normaliser: str) -> dict[str, dict[str, Any]] | None:
    """Bench's latest score per model (by catalog model id) on the newest version Bench has of this Dataset
    Catalog dataset, or None when Bench is not linked, has no version of it (404), or does not answer in time."""
    if not usable():
        return None
    try:
        with client(SCORES_TIMEOUT_S) as c:
            r = c.get(f"/api/datasets/by-catalog/{catalog_dataset_id}/scores", params={"normaliser": normaliser})
    except httpx.HTTPError:
        mark_unreachable()
        return None
    if r.status_code != 200:
        return None
    try:
        body = r.json()
        ds = body.get("dataset") or {}
        out = {}
        for s in body.get("scores") or []:
            out[s["model"]] = {"wer": s.get("wer"), "run_id": s.get("run_id"), "score_id": s.get("score_id"),
                               "normaliser": s.get("normaliser"), "created_at": s.get("created_at"),
                               "dataset": ds.get("name"), "dataset_id": ds.get("id"),
                               "catalog_version": ds.get("catalog_version")}
        return out
    except (ValueError, AttributeError, KeyError, TypeError):
        return None
