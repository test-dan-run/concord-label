# syntax=docker/dockerfile:1
# Concord Label: just build  ->  concord-label:0.2.0 (API + worker on port 8620)
#
# Improve (Phase 3, concord_improve) is on hold and installed in no image; a concord-label-full target comes
# back when Phase 3 is scoped (SPLIT_PLAN.md §3.2).
#
# The platform's aicore client is a sibling repo, so it comes in through a named build context:
#   docker build --build-context platform=../../platform --target label -t concord-label:0.2.0 .
#
# Running: join ai-net, where the Model Catalog answers at http://catalog:8700 (it starts and serves every
# model; Label only takes leases and calls its gateway). Mount the platform directory at /platform. See
# README.md.
#
# The Dataset Catalog (same service): mount its store read-only and point DATASET_STORE at the mount, e.g.
#   -v ${AI_DATA:-~/ai-data}/datasets:/datasets:ro -e DATASET_STORE=/datasets
# Label then keeps new audio in the store and publishes to the catalog. Without it, originals stay in /data.

ARG PYTHON=python:3.12.15-slim-bookworm
ARG NODE=node:22.23.3-bookworm-slim

# ---------------------------------------------------------------- frontend
FROM ${NODE} AS web-base
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- tools copied into the images
FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

# ---------------------------------------------------------------- Python environments (built with a compiler)
FROM ${PYTHON} AS pybuild
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
# pyproject's path dependency is ../../platform/clients/aicore: from /srv/apps/concord-label that is
# /srv/platform/clients/aicore (installed non-editable; the platform itself is mounted at /platform).
COPY --from=platform clients/aicore /srv/platform/clients/aicore
WORKDIR /srv/apps/concord-label
COPY pyproject.toml uv.lock README.md ./
COPY backend/ backend/
RUN uv sync --frozen --no-dev --no-editable && rm -rf /opt/venv/lib/python3.12/site-packages/concord_improve

# ---------------------------------------------------------------- runtime
FROM ${PYTHON} AS label
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
COPY --from=pybuild /opt/venv /opt/venv
WORKDIR /srv/apps/concord-label
COPY README.md ./
COPY fixtures/generated/templates fixtures/generated/templates
ENV CONCORD_HOME=/srv/apps/concord-label CONCORD_DATA=/data PLATFORM_DIR=/platform \
    AI_HOST={name} AICORE_ENDPOINTS=/platform/clients/aicore/src/aicore/endpoints.generated.yaml \
    CATALOG_URL=http://catalog:8700 AICORE_CATALOG=http://catalog:8700 PUBLIC_URL=http://concord-label:8620
VOLUME /data
COPY --from=web-base /web/dist /srv/apps/concord-label/frontend/dist
EXPOSE 8620
CMD ["sh", "-c", "python -m concord_label.cli migrate && (python -m concord_label.runs.worker &) && exec uvicorn concord_label.api.app:app --host 0.0.0.0 --port 8620"]
