"""Server for the Playwright tests: a fresh data dir seeded through Label's own API on recorded responses
(models from the replay catalog, an annotated dataset, and an audio-only copy of the same audio
pseudo-labelled by pyannote -> both Whispers), then the API + built frontend on --port. No GPU, platform or
Model Catalog needed: in replay mode the catalog is a stand-in built from fixtures/recorded.

The Dataset Catalog is set up with aicore's FakeDatasetCatalog and a store in the data dir, unless
E2E_DATASET_CATALOG=0 or the installed aicore has no Dataset Catalog client; Label then sends to Bench as before.
With it, the store also holds "Raw calls": v1 with audio only, made into a Label dataset of the same name, and
v2 published by Concord Bench afterwards, so a publish from that dataset asks first.

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

    dc = dataset_catalog(work / "store")

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
        if dc is not None:
            seed_catalog(client, dc, log)
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


def dataset_catalog(store: Path):
    """A FakeDatasetCatalog with its store at `store`, set up as Label's Dataset Catalog; None when turned off."""
    if os.environ.get("E2E_DATASET_CATALOG", "1") == "0":
        return None
    try:
        from aicore.datasets import FakeDatasetCatalog
    except ImportError:
        print("aicore has no Dataset Catalog client: running without the Dataset Catalog", flush=True)
        return None
    from concord_label.core.settings import APP
    from concord_label.platform import datasets

    os.environ["DATASET_STORE"] = str(store)
    dc = FakeDatasetCatalog(store, app=APP)
    datasets.set_dataset_catalog(dc)
    return dc


def seed_catalog(client, dc, log) -> None:
    import io

    import numpy as np
    import soundfile as sf

    from concord_label.core.settings import APP
    from concord_label.runs import jobs

    def tone(seconds: float, freq: float) -> bytes:
        t = np.arange(int(seconds * 16000)) / 16000
        buf = io.BytesIO()
        sf.write(buf, 0.3 * np.sin(2 * np.pi * freq * t), 16000, subtype="PCM_16", format="WAV")
        return buf.getvalue()

    audio = ROOT / "fixtures" / "generated" / "dataset" / "audio"
    raw = dc.create_dataset("Raw calls", language="en", description="Calls to label, uploaded to the catalog",
                            tags=["raw"])
    dc.add_version(raw["id"], {"calls/d02_8k.wav": (audio / "d02_8k.wav").read_bytes(),
                               "calls/d05_clipped.wav": (audio / "d05_clipped.wav").read_bytes()}, label_types=())
    r = client.post("/api/datasets/from-catalog", json={"catalog_dataset_id": raw["id"], "version": 1})
    assert r.status_code == 201, r.text
    jobs.run_pending()
    dc.app = "concord-bench"
    try:
        dc.publish_audio(raw["id"], {"calls/tone.wav": dc.add_audio(tone(3.0, 220))}, parent=1)
    finally:
        dc.app = APP
    log(f"dataset catalog: Raw calls ({raw['id']}) v1 made into Label dataset {r.json()['dataset']['id']}, v2 from "
        "concord-bench")


if __name__ == "__main__":
    raise SystemExit(main())
