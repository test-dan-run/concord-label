"""The Dataset Catalog, as Concord Label uses it (DATASET_CATALOG_PLAN.md §8; platform/catalog/README.md).

The Dataset Catalog is set up when DATASET_STORE names the store's mount. Label then:

- sends each original to the store at ingest (`upload_audio`) and holds it, and releases the holds when it
  deletes the last dataset that uses the audio (datasets/ingest.py, api/datasets.py);
- publishes versions of a dataset (label/publish.py) and makes datasets from versions (label/from_catalog.py).

The catalog service is the model catalog's (CATALOG_URL), and the client is aicore's `DatasetCatalog`. Label reads
audio and version folders from the mount, never over HTTP. Without DATASET_STORE nothing here is called, and
aicore.datasets is never imported, so Label works with an aicore that has no Dataset Catalog client.

Tests swap in a `FakeDatasetCatalog` with `set_dataset_catalog`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import httpx
from aicore.catalog import CatalogError

from ..core.settings import APP, get_settings

_override: Any = None


class Unavailable(Exception):
    """The Dataset Catalog is not set up, did not answer, or refused a call. `status` is its HTTP status (None when it
    did not answer), and `error` the client's exception."""

    def __init__(self, message: str, status: int | None = None, error: Exception | None = None):
        super().__init__(message)
        self.status, self.error = status, error


def set_dataset_catalog(dc: Any) -> None:
    """Use this client (tests: a FakeDatasetCatalog) instead of the configured one; None goes back to the setting."""
    global _override
    _override = dc


def enabled() -> bool:
    return get_settings().dataset_store is not None


@lru_cache(maxsize=4)
def _client(url: str, store: str) -> Any:
    from aicore.datasets import DatasetCatalog

    return DatasetCatalog(url, app=APP, store=store)


def get() -> Any:
    """The client: a DatasetCatalog on CATALOG_URL with the store at DATASET_STORE. Raises Unavailable when not set up."""
    if _override is not None:
        return _override
    s = get_settings()
    if s.dataset_store is None:
        raise Unavailable("the Dataset Catalog is not set up (DATASET_STORE is not set)")
    return _client(s.catalog_root, str(s.dataset_store.expanduser()))


def where() -> str:
    return getattr(_override, "url", None) or get_settings().catalog_root


@contextmanager
def calls(what: str) -> Iterator[None]:
    """Turn the client's errors into Unavailable, with `what` Label was doing and the catalog's message."""
    try:
        yield
    except CatalogError as exc:
        status = getattr(exc, "status", None)
        raise Unavailable(f"{what}: the Dataset Catalog at {where()} answered {status}: {getattr(exc, 'message', exc)}",
                          status, exc) from None
    except httpx.HTTPError as exc:
        raise Unavailable(f"{what}: the Dataset Catalog at {where()} did not answer ({type(exc).__name__})",
                          None, exc) from None


def ui_url(dataset_id: str | None = None, n: int | None = None) -> str:
    """A page of the Dataset Catalog UI: the list, a dataset, or one of its versions."""
    base = get_settings().catalog_ui + "/datasets"
    if dataset_id is None:
        return base
    return f"{base}/{dataset_id}" + (f"/v/{n}" if n is not None else "")


def config() -> dict[str, Any]:
    """/api/config's `dataset_catalog`."""
    s = get_settings()
    on = enabled()
    return {"enabled": on, "store": str(s.dataset_store) if on else None, "url": where() if on else None,
            "ui_url": ui_url() if on else None}
