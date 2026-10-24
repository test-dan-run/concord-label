"""`python -m concord_label.cli <command>`: migrate, revision and move-originals. Offline kits are the model
catalog's.

Recording model responses (`record`) is Concord Bench's: Bench re-records `fixtures/recorded` and Label
copies it (README.md)."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="concord_label")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create data folders and bring the database to the latest schema")
    rv = sub.add_parser("revision", help="autogenerate an Alembic revision from model changes")
    rv.add_argument("message")
    sub.add_parser("move-originals", help="move data/originals/ into the Dataset Catalog's store (DATASET_STORE)")
    args = ap.parse_args(argv)

    from .core import db

    try:
        if args.cmd in ("migrate", "revision", "move-originals"):
            db.migrate()
    except db.ForeignDatabase as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "move-originals":
        from .move_originals import NotSetUp, move_originals

        try:
            return move_originals()
        except NotSetUp as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.cmd == "migrate":
        print("database at", db.db_url())
    elif args.cmd == "revision":
        from alembic import command

        command.revision(db.alembic_config(), message=args.message, autogenerate=True, rev_id=db.next_revision_id())
    return 0


if __name__ == "__main__":
    sys.exit(main())
