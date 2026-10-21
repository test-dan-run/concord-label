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
    uv run uvicorn concord_label.api.app:app --reload --reload-dir backend --port ${CONCORD_PORT:-8610} &
    {{py}} -m concord_label.runs.worker &
    (cd frontend && npm run dev -- --port ${CONCORD_WEB_PORT:-5174}) &
    wait

# API only (serves the built frontend from frontend/dist when present)
api:
    {{py}} -m concord_label.cli migrate
    uv run uvicorn concord_label.api.app:app --port ${CONCORD_PORT:-8610}

# job worker only
worker:
    {{py}} -m concord_label.runs.worker

# bring the database to the latest schema
migrate:
    {{py}} -m concord_label.cli migrate

# new Alembic revision from model changes: just revision "add foo"
revision message:
    {{py}} -m concord_label.cli revision "{{message}}"

# backend tests (no GPU / platform needed) + frontend unit tests
test *args:
    uv run pytest {{args}}
    cd frontend && npm run test --silent

# adapter / orchestration tests against live platform services (starts what they need)
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

# ruff + import-linter (concord_label must not import Improve) + frontend typecheck
lint:
    uv run ruff check backend tests
    uv run lint-imports
    cd frontend && npm run lint --silent

# check contracts/samples (CONTRACTS.md) and compare with the other app's repository next to this one
check-contracts:
    {{py}} contracts/check.py

# offline kit of platform services: just export-offline NAME... [--kind full|models] [--out DIR] [--concord-image IMG]
export-offline +args:
    {{py}} -m concord_label.cli export-offline {{args}}

# check an offline kit's files against its manifest.json
verify-kit dir:
    {{py}} -m concord_label.cli verify-kit {{dir}}

# frontend production build into frontend/dist
build-web:
    cd frontend && npm run build

# concord-label image (API + worker on 8610); the platform's aicore client comes in as a named build context
build:
    docker build --build-context platform=${PLATFORM_DIR:-../../platform} --target label -t {{image}} .
