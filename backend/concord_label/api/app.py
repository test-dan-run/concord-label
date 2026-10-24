"""FastAPI app. Serves `/api/*` and, when built, the frontend.

Improve (Phase 3, `concord_improve`) is on hold and installed in no image, so nothing mounts it here. When
Phase 3 is scoped, a `concord-label-full` image brings back a feature flag that mounts its router at
`/api/improve` (SPLIT_PLAN.md §3.2).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core import db
from ..core.settings import VERSION, get_settings
from ..platform import catalog
from . import audio, config, dataset_catalog, datasets, jobs, label, lexicons, models, outputs, references, sync

log = logging.getLogger("concord_label")


def create_app(migrate: bool = True) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if migrate:
            try:
                db.migrate()
            except db.ForeignDatabase as exc:
                log.error("%s", exc)
                raise
        # Where the catalog and Concord Bench read Label's outputs (POST /api/outputs/batch). In the
        # background, retried until the catalog answers: Label works without it.
        catalog.register_app_in_background()
        yield

    app = FastAPI(title="Concord Label", version=VERSION, lifespan=lifespan)
    for r in (config.router, datasets.router, models.router, jobs.router, audio.router, label.router, sync.router,
              references.router, outputs.router, lexicons.router, dataset_catalog.router):
        app.include_router(r, prefix="/api")

    dist = settings.frontend_dist
    if (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "Not Found")  # unknown API routes never fall through to the SPA
            f = (dist / path).resolve()
            if path and f.is_file() and f.is_relative_to(dist.resolve()):
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
