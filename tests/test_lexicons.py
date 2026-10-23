"""Lexicons: entries, matching (mirrored by frontend lib/lexicon.ts), clashes, the API, usage counts and the
spelling hints an annotator keeps."""

from __future__ import annotations

from concord_label.core.models import AudioFile, Dataset, Segment, SegmentStatus
from concord_label.label import lexicon as lex
from label_helpers import _ok


def test_entries_upsert_and_remove():
    e = lex.upsert([], " lah ", ["la", "LA", "lah", " laa ", ""])
    assert e == [{"word": "lah", "variants": ["la", "laa"]}]
    e = lex.upsert(e, "okay", ["ok"])
    assert [x["word"] for x in e] == ["okay", "lah"]  # new entries go on top
    e = lex.upsert(e, "LAH", ["lar", "la"])
    assert e[1] == {"word": "lah", "variants": ["la", "laa", "lar"]}  # merged in place
    assert lex.remove(e, "Okay") == [e[1]]


def _matcher(entries):
    rules = {}
    for x in entries:
        for v in x["variants"]:
            rules[lex.norm(v)] = lex.Rule(x["word"], 1, "L", 1)
    return lex.Matcher(rules)


def test_hints_are_whole_words_case_kept_longest_first():
    m = _matcher([{"word": "lah", "variants": ["la"]}, {"word": "walao", "variants": ["wah lau", "wah"]}])
    text = "Okay La, no problem. LA? la's lala wah  lau, wah."
    hints = m.hints(text)
    assert [(h["found"], h["word"]) for h in hints] == [("La", "Lah"), ("LA", "LAH"), ("wah  lau", "walao"),
                                                        ("wah", "walao")]
    assert text[hints[0]["start"]:hints[0]["end"]] == "La"
    assert [h["found"] for h in m.hints(text, kept=["LA"])] == ["wah  lau", "wah"]
    assert m.hints(None) == [] and lex.Matcher({}).hints("la") == []
    assert len(m.hints("laé la")) == 1  # letters outside ASCII are part of a word


def test_problems_name_ambiguous_variants():
    a = ("Singlish", [{"word": "lah", "variants": ["la"]}, {"word": "okay", "variants": ["ok"]}])
    assert lex.problems([a]) == []
    b = ("Other", [{"word": "lar", "variants": ["la"]}, {"word": "ok", "variants": ["okk"]}])
    found = lex.problems([a, b])
    assert any(p == "“la” is flagged for both “lah” (in Singlish) and “lar” (in Other)" for p in found)
    assert any(p == "“ok”, a variant of “okay” (in Singlish), is also a house spelling (in Other)" for p in found)


def _confirmed(session, texts):
    ds = Dataset(name="Calls", status="ready")
    session.add(ds)
    session.flush()
    f = AudioFile(dataset_id=ds.id, rel_path="a.wav", sha256="a" * 64, ext=".wav", duration=60.0)
    session.add(f)
    session.flush()
    segs = []
    for k, (text, status) in enumerate(texts):
        s = Segment(audio_file_id=f.id, start=k, end=k + 1, text=text, status=status)
        session.add(s)
        segs.append(s)
    session.commit()
    return ds, f, segs


def test_api_draft_publish_discard_and_counts(client, session):
    ds, f, segs = _confirmed(session, [("Okay la, ok lah.", SegmentStatus.confirmed),
                                       ("la la la", SegmentStatus.pseudo)])  # unconfirmed: not counted
    lx = _ok(client.post("/api/lexicons", json={"name": "Singlish particles"}), 201)
    assert lx["version"] is None and lx["draft"] == [] and not lx["dirty"]
    lx = _ok(client.post(f"/api/lexicons/{lx['id']}/entries", json={"word": "lah", "variants": ["la", "laa"]}))
    lx = _ok(client.post(f"/api/lexicons/{lx['id']}/entries", json={"word": "okay", "variants": ["ok"]}))
    assert lx["dirty"] and [e["word"] for e in lx["draft"]] == ["okay", "lah"]
    lah = lx["draft"][1]
    assert (lah["house"], lah["variant"]) == (1, 1)
    assert lah["review"] == {"dataset_id": ds.id, "file_id": f.id, "segment_id": segs[0].id}
    assert _ok(client.get("/api/lexicons/active"))["lexicons"] == []  # nothing published yet

    lx = _ok(client.post(f"/api/lexicons/{lx['id']}/publish"))
    assert lx["version"] == 1 and not lx["dirty"] and lx["versions"][0]["entries"] == 2
    _ok(client.post(f"/api/lexicons/{lx['id']}/publish"), 409)  # nothing new
    act = _ok(client.get("/api/lexicons/active"))["lexicons"]
    assert act == [{"id": lx["id"], "name": "Singlish particles", "version": 1,
                    "entries": [{"word": "okay", "variants": ["ok"]}, {"word": "lah", "variants": ["la", "laa"]}]}]
    row = _ok(client.get("/api/lexicons"))[0]
    assert (row["version"], row["entries"], row["datasets"], row["dirty"]) == (1, 2, 1, False)

    lx = _ok(client.post(f"/api/lexicons/{lx['id']}/entries/remove", json={"word": "okay"}))
    assert lx["dirty"] and len(lx["draft"]) == 1
    _ok(client.post(f"/api/lexicons/{lx['id']}/entries/remove", json={"word": "nope"}), 422)
    lx = _ok(client.post(f"/api/lexicons/{lx['id']}/discard"))
    assert not lx["dirty"] and len(lx["draft"]) == 2

    # a second lexicon that flags "la" for another spelling cannot be published
    other = _ok(client.post("/api/lexicons", json={"name": "Other"}), 201)
    other = _ok(client.post(f"/api/lexicons/{other['id']}/entries", json={"word": "lar", "variants": ["la"]}))
    assert other["problems"] == ["“la” is flagged for both “lar” (in Other) and “lah” (in Singlish particles)"]
    r = client.post(f"/api/lexicons/{other['id']}/publish")
    assert r.status_code == 409 and "lar" in r.json()["detail"]
    _ok(client.delete(f"/api/lexicons/{other['id']}"), 204)
    assert [x["name"] for x in _ok(client.get("/api/lexicons"))] == ["Singlish particles"]


def test_kept_spellings_are_saved_on_the_segment(client, session):
    _, _, segs = _confirmed(session, [("Okay la", SegmentStatus.pseudo)])
    s = _ok(client.patch(f"/api/label/segments/{segs[0].id}", json={"spelling_kept": ["LA", " la ", "Wah  Lau"]}))
    assert s["spelling_kept"] == ["la", "wah lau"] and s["reviewed"]
    s = _ok(client.patch(f"/api/label/segments/{segs[0].id}", json={"text": "Okay la!"}))
    assert s["spelling_kept"] == ["la", "wah lau"]  # other edits keep them
