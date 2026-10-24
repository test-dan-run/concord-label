"""Settings, read from the environment (and `.env`). One instance, `settings`, used everywhere."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP = "concord-label"
VERSION = "0.2.0"
CONTRACT_VERSION = 2  # CONTRACTS.md

# The Concord Label checkout (fixtures, frontend/dist, .env). In an image the package is installed into
# site-packages, so CONCORD_HOME says where the app's files are.
REPO_ROOT = Path(os.environ.get("CONCORD_HOME") or Path(__file__).resolve().parents[3])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # API port of `just dev` / `just api` (the image always listens on 8620 inside the container).
    concord_port: int = 8620
    # Audio, derived files and the database (Q2: local disk only).
    concord_data: Path = Field(default=REPO_ROOT / "data")
    # Phase 3 (Improve) is on hold and installed in no image, so there is no FEATURE_IMPROVE flag. A
    # concord-label-full image brings it back when Phase 3 is scoped (SPLIT_PLAN.md §3.2).
    # Platform repo: the aicore endpoint map is generated here.
    platform_dir: Path = Field(default=REPO_ROOT.parent.parent / "platform")
    # The model catalog (platform/catalog/README.md): models, leases, the output index and the gateway every
    # inference call goes through. CATALOG_URL, else AICORE_CATALOG; `http://catalog:8700` in a container.
    catalog_url: str = Field(default="http://127.0.0.1:8700",
                             validation_alias=AliasChoices("catalog_url", "aicore_catalog"))
    # Where people open the catalog's own pages (registering models, verification reports); default catalog_url.
    catalog_ui_url: str | None = None
    # Where the catalog and Concord Bench reach this app's API (POST /api/outputs/batch). Default
    # http://127.0.0.1:<concord_port>; `http://concord-label:8620` in a container on ai-net.
    public_url: str | None = Field(default=None, validation_alias=AliasChoices("concord_public_url", "public_url"))
    # Passed through to aicore; unset means the map bundled with the aicore package.
    aicore_endpoints: Path | None = None
    # localhost on the host; `{name}` inside a container on ai-net.
    ai_host: str = "localhost"
    # Serve recorded model responses from this folder instead of calling services (tests, demos). The
    # catalog is then an in-memory stand-in built from the recordings, so no catalog needs to run.
    replay_dir: Path | None = None
    # Write every live model response into this folder (to refresh recordings).
    record_dir: Path | None = None
    # Concord Bench (SPLIT_PLAN.md §5): where "Send to Bench" pushes reference packages and where the
    # live evaluation reads locked scores. Unset: packages are downloaded and the Locked column is hidden.
    bench_url: str | None = None
    # The Dataset Catalog's store (DATASET_CATALOG_PLAN.md §4), mounted read-only. Set, Label sends new audio to
    # the store through the catalog service at catalog_url, reads originals from the store, publishes versions to
    # the Dataset Catalog and makes datasets from them. Unset, originals stay in data/originals/ as before.
    dataset_store: Path | None = None
    # Built frontend served by FastAPI; absent in development (Vite serves it).
    frontend_dist: Path = Field(default=REPO_ROOT / "frontend" / "dist")

    @property
    def catalog_root(self) -> str:
        return self.catalog_url.strip().rstrip("/")

    @property
    def catalog_ui(self) -> str:
        return (self.catalog_ui_url or self.catalog_root).rstrip("/")

    @property
    def public_base_url(self) -> str:
        return (self.public_url or f"http://127.0.0.1:{self.concord_port}").rstrip("/")

    def export_platform_env(self) -> None:
        """aicore reads AI_HOST, AICORE_ENDPOINTS and AICORE_CATALOG from the process environment. With
        AICORE_CATALOG set, `aicore.client(name)` goes through the catalog's gateway."""
        os.environ["AI_HOST"] = self.ai_host
        os.environ["AICORE_CATALOG"] = self.catalog_root
        if self.aicore_endpoints:
            os.environ["AICORE_ENDPOINTS"] = str(self.aicore_endpoints)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.export_platform_env()
    return s


def reset_settings() -> None:
    """Tests change the environment, then call this."""
    get_settings.cache_clear()
