"""One-off: re-key recorded model responses for catalog-era params (no `image` in the adapter params).

Recordings live at `<dir>/<service>/<sha16>_<span>_<params hash>.json`, and each file stores the params it
was recorded with. Before the model catalog those params held the pinned image tag; now the build key is kept
beside the params, not in them (adapters/__init__.py). This drops `image` from each file's params and renames
the file to the hash of what is left, so nothing has to be recorded again. Idempotent.

    uv run python fixtures/rekey_recordings.py fixtures/recorded [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aicore.catalog import params_hash


def recording_name(audio: dict, phash: str) -> str:
    """The same key as adapters.base.AudioRef.key."""
    start, end = float(audio.get("start", 0.0)), float(audio.get("end", -1.0))
    span = "full" if end < 0 else f"{start:.3f}-{end:.3f}"
    return f"{audio['sha256'][:16]}_{span}_{phash}.json"


def rekey(root: Path, dry_run: bool = False) -> tuple[int, int]:
    renamed = unchanged = 0
    for path in sorted(root.glob("*/*.json")):
        rec = json.loads(path.read_text())
        if not isinstance(rec, dict) or "params" not in rec or "audio" not in rec:
            continue
        params = {k: v for k, v in rec["params"].items() if k != "image"}
        target = path.with_name(recording_name(rec["audio"], params_hash(params)))
        if target == path and params == rec["params"]:
            unchanged += 1
            continue
        rec["params"] = params
        text = json.dumps(rec, indent=None) + "\n"
        if target != path and target.exists() and json.loads(target.read_text()) != rec:
            raise SystemExit(f"{target} exists with other content; not overwriting it with {path.name}")
        print(f"{path.relative_to(root)} -> {target.name}")
        if not dry_run:
            target.write_text(text)
            if target != path:
                path.unlink()
        renamed += 1
    return renamed, unchanged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="the recordings folder (fixtures/recorded)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    renamed, unchanged = rekey(args.root, args.dry_run)
    print(f"{renamed} re-keyed, {unchanged} already current", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
