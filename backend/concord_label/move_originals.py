"""`just move-originals`: move the originals in data/originals/ into the Dataset Catalog's store
(DATASET_CATALOG_PLAN.md §12).

1. Every original goes to the store through `upload_audio`, which checks its sha256, and Label holds it.
2. data/originals/ is renamed to data/originals.moved/, so Label reads the store from then on. When
   originals.moved/ is there already (Sync brought more audio since the last run), the new files move into it.
3. The person deletes data/originals.moved/ when they're satisfied. This command never deletes anything.

Nothing is renamed unless every original is in the store and visible at DATASET_STORE, so a store mounted somewhere
else never leaves Label without its audio.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .core import paths
from .core.settings import get_settings
from .platform import datasets as dataset_catalog

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class NotSetUp(RuntimeError):
    pass


def move_originals(log: Callable[[str], None] = print) -> int:
    """0 when the originals moved, 1 when some could not be stored (nothing renamed)."""
    if not dataset_catalog.enabled():
        raise NotSetUp("set DATASET_STORE to the Dataset Catalog's store first (DATASET_CATALOG_PLAN.md §4)")
    src, dest = paths.data_root() / "originals", paths.data_root() / "originals.moved"
    entries = sorted(p for p in src.iterdir() if p.is_file()) if src.is_dir() else []
    audio = [(p, p.name[:64]) for p in entries if HEX64.match(p.name[:64])]
    if not entries:
        log(f"nothing to move: {src} is empty or missing")
        return 0
    for p in entries:
        if not HEX64.match(p.name[:64]):
            log(f"not an original (no sha256 in its name), moved along unchanged: {p.name}")
    dc = dataset_catalog.get()
    failed: list[str] = []
    for k, (p, sha) in enumerate(audio):
        try:
            with dataset_catalog.calls(f"storing {p.name}"):
                dc.upload_audio(p, sha)
        except dataset_catalog.Unavailable as exc:
            failed.append(p.name)
            log(str(exc))
            continue
        if (k + 1) % 100 == 0:
            log(f"stored {k + 1} of {len(audio)}")
    shas = sorted({sha for p, sha in audio if p.name not in failed})
    try:
        with dataset_catalog.calls("holding the originals"):
            held = dc.hold(shas)
    except dataset_catalog.Unavailable as exc:
        log(str(exc))
        return 1
    log(f"stored and held {len(shas)} originals (Concord Label now holds {held} files)")
    unseen = [s for s in shas if not paths.store_audio(s).is_file()]
    if failed or unseen:
        if unseen:
            log(f"{len(unseen)} stored files are not visible at DATASET_STORE={get_settings().dataset_store}: is "
                "that where the catalog's store is mounted?")
        log(f"{src} was left in place; fix the problems above and run this again")
        return 1
    if not dest.exists():
        src.rename(dest)
    else:
        for p in entries:
            target = dest / p.name
            if target.exists():
                log(f"kept {p.name} in {src}: {dest} has a file of that name already")
            else:
                p.rename(target)
    log(f"moved {src} to {dest}; delete it when you're satisfied that Label reads the store")
    return 0
