"""Lexicons: one house spelling for words annotators write more than one way (`lah` over `la`, `laa`).

Every lexicon applies to every dataset. Its entries are edited as a draft; publishing makes a new version,
and Annotate and the counts here use each lexicon's latest version. A variant in a segment's text is a
spelling hint in Annotate (frontend `lib/lexicon.ts` matches exactly as `Matcher` does here); it is never
changed for the annotator. A hint the annotator chose to keep is listed in the segment's
`meta.spelling_kept` (lower case) and not shown again for that segment.

Matching is case-insensitive on whole words: a term is not matched inside a longer word, and an
apostrophe counts as part of a word (`la` is not found in `la's`). A term of several words matches with
any run of spaces between them. Where terms overlap, the longest wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import AudioFile, Lexicon, Segment, SegmentStatus


class LexiconError(ValueError):
    pass


def norm(term: str) -> str:
    """A term as compared: lower case, single spaces."""
    return " ".join(term.lower().split())


def entry(word: str, variants: Iterable[str]) -> dict[str, Any]:
    """A clean entry: the house spelling trimmed; variants trimmed, without repeats or the house spelling."""
    word = " ".join(word.split())
    if not word:
        raise LexiconError("an entry needs a house spelling")
    seen = {norm(word)}
    out = []
    for v in variants:
        v = " ".join(v.split())
        if v and norm(v) not in seen:
            seen.add(norm(v))
            out.append(v)
    return {"word": word, "variants": out}


def upsert(entries: list[dict[str, Any]], word: str, variants: Iterable[str]) -> list[dict[str, Any]]:
    """`entries` with `word` added at the top, or, if it is there already, its variants merged in place."""
    new = entry(word, variants)
    out = [dict(e) for e in entries]
    for k, e in enumerate(out):
        if norm(e["word"]) == norm(new["word"]):
            out[k] = entry(e["word"], [*e["variants"], *new["variants"]])
            return out
    return [new, *out]


def remove(entries: list[dict[str, Any]], word: str) -> list[dict[str, Any]]:
    out = [e for e in entries if norm(e["word"]) != norm(word)]
    if len(out) == len(entries):
        raise LexiconError(f"no entry {word!r}")
    return out


def published(lx: Lexicon) -> tuple[int, list[dict[str, Any]]] | None:
    """(version, entries) of the latest published version, or None before the first publish."""
    return (lx.versions[-1].version, lx.versions[-1].entries) if lx.versions else None


def _pattern(term: str) -> str:
    return r"\s+".join(re.escape(w) for w in term.split())


def _regex(terms: Iterable[str]) -> re.Pattern[str] | None:
    terms = sorted({norm(t) for t in terms if t.strip()}, key=lambda t: (-len(t), t))
    if not terms:
        return None
    return re.compile(r"(?<![\w'])(?:" + "|".join(_pattern(t) for t in terms) + r")(?![\w'])", re.IGNORECASE)


def with_case(found: str, word: str) -> str:
    """The house spelling in the case of the text it replaces: `La` -> `Lah`, `LA` -> `LAH`."""
    if len(found) > 1 and found.isupper():
        return word.upper()
    if found[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


@dataclass(frozen=True)
class Rule:
    word: str
    lexicon_id: int
    lexicon: str
    version: int


class Matcher:
    """Finds variants of the published lexicons in a text."""

    def __init__(self, rules: dict[str, Rule]):
        self.rules = rules  # norm(variant) -> rule
        self.regex = _regex(rules)

    @classmethod
    def published(cls, session: Session) -> Matcher:
        """Every lexicon's latest version; where two give one variant different house spellings, the older
        lexicon wins (the Lexicons page lists the clash)."""
        rules: dict[str, Rule] = {}
        for lx in session.scalars(select(Lexicon).order_by(Lexicon.id)):
            pub = published(lx)
            if pub is None:
                continue
            for e in pub[1]:
                for v in e["variants"]:
                    rules.setdefault(norm(v), Rule(e["word"], lx.id, lx.name, pub[0]))
        return cls(rules)

    def hints(self, text: str | None, kept: Iterable[str] = ()) -> list[dict[str, Any]]:
        if not text or self.regex is None:
            return []
        skip = {norm(k) for k in kept}
        out = []
        for m in self.regex.finditer(text):
            key = norm(m.group(0))
            if key in skip or key not in self.rules:
                continue
            r = self.rules[key]
            out.append({"start": m.start(), "end": m.end(), "found": m.group(0), "word": with_case(m.group(0), r.word),
                        "lexicon_id": r.lexicon_id})
        return out


def problems(lexicons: list[tuple[str, list[dict[str, Any]]]]) -> list[str]:
    """Clashes that would make a hint ambiguous, across the given (name, entries): one variant with two
    different house spellings, or a variant that is another entry's house spelling."""
    out: list[str] = []
    owner: dict[str, tuple[str, str]] = {}  # norm(variant) -> (house spelling, lexicon)
    houses: dict[str, tuple[str, str]] = {}  # norm(house spelling) -> (house spelling, lexicon)
    for name, entries in lexicons:
        for e in entries:
            houses.setdefault(norm(e["word"]), (e["word"], name))
    for name, entries in lexicons:
        for e in entries:
            for v in e["variants"]:
                k = norm(v)
                if k in owner and norm(owner[k][0]) != norm(e["word"]):
                    w, n = owner[k]
                    where = "" if n == name else f" (in {n})"
                    out.append(f"“{v}” is flagged for both “{w}”{where} and “{e['word']}” (in {name})")
                owner.setdefault(k, (e["word"], name))
                if k in houses and norm(houses[k][0]) != norm(e["word"]):
                    w, n = houses[k]
                    out.append(f"“{v}”, a variant of “{e['word']}” (in {name}), is also a house spelling (in {n})")
    return list(dict.fromkeys(out))


def usage(session: Session, lexicons: list[tuple[int, list[dict[str, Any]]]]) -> dict[int, dict[str, Any]]:
    """How often each entry's house spelling and variants appear in confirmed segments, across all datasets,
    in one pass. Per lexicon: {"datasets": n, "entries": {norm(word): {"house", "variant", "review"}}}, where
    `review` is the first confirmed segment holding one of the variants ({dataset_id, file_id, segment_id})."""
    terms: dict[str, list[tuple[int, str, str]]] = {}  # norm(term) -> [(lexicon id, norm(word), kind)]
    out: dict[int, dict[str, Any]] = {}
    for lid, entries in lexicons:
        out[lid] = {"datasets": set(), "entries": {}}
        for e in entries:
            w = norm(e["word"])
            out[lid]["entries"][w] = {"house": 0, "variant": 0, "review": None}
            terms.setdefault(w, []).append((lid, w, "house"))
            for v in e["variants"]:
                terms.setdefault(norm(v), []).append((lid, w, "variant"))
    rx = _regex(terms)
    if rx is not None:
        rows = session.execute(
            select(Segment.id, Segment.text, Segment.audio_file_id, AudioFile.dataset_id)
            .join(AudioFile, AudioFile.id == Segment.audio_file_id)
            .where(Segment.status == SegmentStatus.confirmed, Segment.text.is_not(None))
            .order_by(AudioFile.dataset_id, AudioFile.rel_path, Segment.start))
        for sid, text, fid, did in rows:
            for m in rx.finditer(text):
                for lid, w, kind in terms.get(norm(m.group(0)), []):
                    e = out[lid]["entries"][w]
                    e[kind] += 1
                    out[lid]["datasets"].add(did)
                    if kind == "variant" and e["review"] is None:
                        e["review"] = {"dataset_id": did, "file_id": fid, "segment_id": sid}
    for v in out.values():
        v["datasets"] = len(v["datasets"])
    return out
