# Concord Label. Run `just` for the recipe list. See README.md.

set dotenv-load := true
set shell := ["bash", "-euo", "pipefail", "-c"]

py := "uv run --quiet python"
image := "concord-label:0.2.0"

# list recipes
default:
    @just --list --unsorted

# install backend (uv) and frontend (npm) dependencies
setup:
    uv sync
    cd frontend && npm install

# API (reload) + worker + Vite dev server; Ctrl-C stops all three
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT INT TERM
    {{py}} -m concord_label.cli migrate
    uv run uvicorn concord_label.api.app:app --reload --reload-dir backend --port ${CONCORD_PORT:-8620} &
    {{py}} -m concord_label.runs.worker &
    (cd frontend && npm run dev -- --port ${CONCORD_WEB_PORT:-5174}) &
    wait

# API only (serves the built frontend from frontend/dist when present)
api:
    {{py}} -m concord_label.cli migrate
    uv run uvicorn concord_label.api.app:app --port ${CONCORD_PORT:-8620}

# job worker only
worker:
    {{py}} -m concord_label.runs.worker

# bring the database to the latest schema
migrate:
    {{py}} -m concord_label.cli migrate

# move data/originals/ into the Dataset Catalog's store (DATASET_STORE): upload and hold each original, then
# rename the folder to data/originals.moved/ (never deleted; remove it yourself when satisfied)
move-originals:
    {{py}} -m concord_label.cli move-originals

# new Alembic revision from model changes: just revision "add foo"
revision message:
    {{py}} -m concord_label.cli revision "{{message}}"

# backend tests (no GPU / platform needed) + frontend unit tests
test *args:
    uv run pytest {{args}}
    cd frontend && npm run test --silent

# tests against the running model catalog (`just catalog-up` in the platform repo; it starts what they lease)
test-live *args:
    uv run pytest -m live -o addopts="--import-mode=importlib" {{args}}

# Playwright tests of the Label screens on replayed fixtures. The API runs in the concord-label image with
# this checkout's backend, tests, fixtures and frontend/dist mounted (PYTHONPATH points at the mounted
# backend); browsers run in the Playwright image (no host libs). Both join a user-defined Docker network.
e2e: build-web
    #!/usr/bin/env bash
    set -euo pipefail
    home=/srv/apps/concord-label
    docker network create concord-label-e2e >/dev/null 2>&1 || true
    docker rm -f concord-label-e2e-api >/dev/null 2>&1 || true
    trap 'docker rm -f concord-label-e2e-api >/dev/null 2>&1' EXIT
    docker run -d --name concord-label-e2e-api --network concord-label-e2e -e PYTHONPATH=$home/backend \
      -v "$PWD/backend":$home/backend:ro -v "$PWD/fixtures":$home/fixtures:ro \
      -v "$PWD/tests":$home/tests:ro -v "$PWD/frontend/dist":$home/frontend/dist:ro \
      ${E2E_IMAGE:-{{image}}} python tests/e2e_server.py --host 0.0.0.0 --port 8621 >/dev/null
    until docker logs concord-label-e2e-api 2>&1 | grep -q "e2e data ready"; do
      docker ps -q -f name=concord-label-e2e-api | grep -q . || { docker logs concord-label-e2e-api; exit 1; }
      sleep 1
    done
    docker run --rm --network concord-label-e2e --ipc=host -v "$PWD/frontend":/work -w /work \
      -e E2E_BASE_URL=http://concord-label-e2e-api:8621 mcr.microsoft.com/playwright:v1.64.1-noble npx playwright test

# round trip with Concord Bench (SPLIT_PLAN.md §10): the concord-label and concord-bench images (`just build` in
# each repository) on one Docker network, replaying fixtures/recorded (each app's catalog is its replay stand-in);
# push and download, versions, locked score, flags
e2e-handoff *args:
    uv run pytest -m handoff -o addopts="--import-mode=importlib" -s tests/test_handoff.py {{args}}

# ruff + import-linter (concord_label must not import Improve) + frontend typecheck
lint:
    uv run ruff check backend tests
    uv run lint-imports
    cd frontend && npm run lint --silent

# check contracts/samples against docs/CONTRACTS.md, and Label's model adapters against the platform's
# contracts (PLATFORM_DIR/contracts: its registry and conformance checks); `just test` runs both too
check-contracts:
    {{py}} contracts/check.py
    uv run pytest -q tests/test_contracts.py tests/test_platform_contracts.py

# frontend production build into frontend/dist
build-web:
    cd frontend && npm run build

# concord-label image (API + worker on 8620); the platform's aicore client comes in as a named build context
build:
    docker build --build-context platform=${PLATFORM_DIR:-../../platform} --target label -t {{image}} .
