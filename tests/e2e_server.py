"""Server for the Playwright tests: a fresh data dir seeded through Label's own API on recorded responses
(models registered from the registry, an annotated dataset, and an audio-only copy of the same audio
pseudo-labelled by pyannote -> both Whispers), then the API + built frontend on --port. No GPU or
platform needed.

    uv run python tests/e2e_server.py --port 8611
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8611)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="concord-label-e2e-"))
    rec = ROOT / "fixtures" / "recorded"
    os.environ.update({"CONCORD_DATA": str(work / "data"), "REPLAY_DIR": str(rec),
                       "AICORE_ENDPOINTS": str(rec / "endpoints.generated.yaml")})
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests")]
    import uvicorn
    from fastapi.testclient import TestClient

    from concord_label.api.app import create_app
    from label_helpers import label, seed

    def log(m: str) -> None:
        print(m, flush=True)

    app = create_app()
    with TestClient(app) as client:
        flow = seed(client, work / "staging", log=log)
        _, res = label(client, flow)
        log(f"label dataset {flow['unlabelled']}: {res['summary']['segments']} segments, "
            f"triage {res['summary']['triage']}")
    # Jobs started from the browser (e.g. re-fusing pseudo-labels) need a worker, as under `just dev`.
    from concord_label.runs import jobs

    def worker() -> None:
        while True:
            if jobs.run_pending(max_jobs=1) == 0:
                time.sleep(0.5)

    threading.Thread(target=worker, daemon=True).start()
    print(f"e2e data ready in {work}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
