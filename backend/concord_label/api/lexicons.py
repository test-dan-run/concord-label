"""Lexicons (label/lexicon.py): list, create, edit the draft, publish, discard, delete; and the published
entries Annotate matches against."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_session
from ..core.models import Lexicon, LexiconVersion
from ..label import lexicon as lex

router = APIRouter(prefix="/lexicons", tags=["lexicons"])


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _get(session: Session, lexicon_id: int) -> Lexicon:
    lx = session.get(Lexicon, lexicon_id)
    if lx is None:
        raise HTTPException(404, "lexicon not found")
    return lx


def _all(session: Session) -> list[Lexicon]:
    return list(session.scalars(select(Lexicon).order_by(Lexicon.id)))


def _dirty(lx: Lexicon) -> bool:
    pub = lex.published(lx)
    return lx.draft != (pub[1] if pub else [])


def _summary(lx: Lexicon, use: dict[str, Any]) -> dict[str, Any]:
    pub = lex.published(lx)
    return {"id": lx.id, "uid": lx.uid, "name": lx.name, "description": lx.description,
            "version": pub[0] if pub else None, "published_at": _iso(lx.versions[-1].published_at) if pub else None,
            "entries": len(lx.draft), "dirty": _dirty(lx), "datasets": use.get("datasets", 0)}


def _problems(session: Session, lx: Lexicon) -> list[str]:
    """Clashes between this lexicon's draft and the other lexicons' published versions."""
    others = [(o.name, lex.published(o)[1]) for o in _all(session) if o.id != lx.id and lex.published(o)]
    return lex.problems([(lx.name, lx.draft), *others])


def _detail(session: Session, lx: Lexicon) -> dict[str, Any]:
    use = lex.usage(session, [(lx.id, lx.draft)])[lx.id]
    entries = [{**e, **use["entries"].get(lex.norm(e["word"]), {"house": 0, "variant": 0, "review": None})}
               for e in lx.draft]
    return {**_summary(lx, use), "draft": entries, "problems": _problems(session, lx),
            "versions": [{"version": v.version, "entries": len(v.entries), "published_at": _iso(v.published_at),
                          "imported_from": v.imported_from} for v in reversed(lx.versions)]}


def _commit(session: Session, fn):
    try:
        out = fn()
    except lex.LexiconError as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from None
    session.commit()
    return out


@router.get("")
def list_lexicons(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    lexicons = _all(session)
    use = lex.usage(session, [(lx.id, lex.published(lx)[1]) for lx in lexicons if lex.published(lx)])
    return [_summary(lx, use.get(lx.id, {})) for lx in lexicons]


@router.get("/active")
def active(session: Session = Depends(get_session)) -> dict[str, Any]:
    """What Annotate matches against: every lexicon's latest version."""
    out = []
    for lx in _all(session):
        pub = lex.published(lx)
        if pub:
            out.append({"id": lx.id, "name": lx.name, "version": pub[0], "entries": pub[1]})
    return {"lexicons": out}


class LexiconIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


@router.post("", status_code=201)
def create(body: LexiconIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    lx = Lexicon(name=body.name.strip(), description=(body.description or "").strip() or None)
    session.add(lx)
    session.commit()
    return _detail(session, lx)


@router.get("/{lexicon_id}")
def detail(lexicon_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return _detail(session, _get(session, lexicon_id))


class LexiconPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


@router.patch("/{lexicon_id}")
def rename(lexicon_id: int, body: LexiconPatch, session: Session = Depends(get_session)) -> dict[str, Any]:
    lx = _get(session, lexicon_id)
    if body.name is not None:
        lx.name = body.name.strip()
    if "description" in body.model_fields_set:
        lx.description = (body.description or "").strip() or None
    session.commit()
    return _detail(session, lx)


@router.delete("/{lexicon_id}", status_code=204)
def delete(lexicon_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(_get(session, lexicon_id))
    session.commit()


class EntryIn(BaseModel):
    word: str
    variants: list[str] = Field(default_factory=list)


@router.post("/{lexicon_id}/entries")
def add_entry(lexicon_id: int, body: EntryIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Add an entry to the draft, or merge its variants into the entry with the same house spelling."""
    lx = _get(session, lexicon_id)

    def go():
        lx.draft = lex.upsert(lx.draft, body.word, body.variants)

    _commit(session, go)
    return _detail(session, lx)


class EntryDelete(BaseModel):
    word: str


@router.post("/{lexicon_id}/entries/remove")
def remove_entry(lexicon_id: int, body: EntryDelete, session: Session = Depends(get_session)) -> dict[str, Any]:
    lx = _get(session, lexicon_id)

    def go():
        lx.draft = lex.remove(lx.draft, body.word)

    _commit(session, go)
    return _detail(session, lx)


@router.post("/{lexicon_id}/publish")
def publish(lexicon_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """The draft becomes the next version. Refused while it clashes with itself or another lexicon."""
    lx = _get(session, lexicon_id)
    if not _dirty(lx):
        raise HTTPException(409, "nothing to publish: the draft is the published version")
    found = _problems(session, lx)
    if found:
        raise HTTPException(409, "fix these first: " + "; ".join(found))
    pub = lex.published(lx)
    lx.versions.append(LexiconVersion(version=(pub[0] if pub else 0) + 1, entries=[dict(e) for e in lx.draft]))
    session.commit()
    return _detail(session, lx)


@router.post("/{lexicon_id}/discard")
def discard(lexicon_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """The draft goes back to the published version (empty before the first publish)."""
    lx = _get(session, lexicon_id)
    pub = lex.published(lx)
    lx.draft = [dict(e) for e in pub[1]] if pub else []
    session.commit()
    return _detail(session, lx)
