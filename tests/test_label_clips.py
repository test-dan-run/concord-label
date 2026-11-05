"""Datasets with one transcript per clip: the setting, whole-clip pseudo-labelling, and the clip list."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command

from concord_label.core import db
from concord_label.core.db import session_scope
from concord_label.core.models import AudioFile, Dataset, Segment, SegmentStatus
from concord_label.core.settings import reset_settings
from concord_label.datasets.ingest import labelling_for
from concord_label.datasets.parsers import RefRecord
from concord_label.label import sync
from concord_label.runs import jobs
from label_helpers import (
    ASR_MODELS,
    _ok,
    audio_only_dataset,
    create_dataset,
    register_models,
    run_clips,
    stage_fixture_dataset,
)


def _clip_dataset(client, tmp_path, name="Short clips") -> int:
    src = stage_fixture_dataset(tmp_path / name.replace(" ", "_"), refs=None)
    ds = _ok(client.post("/api/datasets", json={"name": name, "language": "en", "tags": [], "local_path": str(src),
                                                "labelling": "clips"}), 201)["dataset"]
    jobs.run_pending()
    assert ds["labelling"] == "clips"
    return ds["id"]


def test_references_pick_the_labelling() -> None:
    assert labelling_for([RefRecord("a", text="hi"), RefRecord("b", text="there")]) == "clips"
    assert labelling_for([RefRecord("a", 0, 1, text="hi"), RefRecord("a", 1, 2, text="there")]) == "turns"
    assert labelling_for([RefRecord("a", 0, 1, speaker="x", text="hi")]) == "turns"
    assert labelling_for([RefRecord("a", 0, 1, speaker="x")]) == "turns"
    assert labelling_for([]) == "turns"


def test_ingest_picks_from_the_references_and_a_person_can_change_it(client, tmp_path) -> None:
    plain = create_dataset(client, stage_fixture_dataset(tmp_path / "plain", refs="asr-jsonl"), "Transcripts")
    assert plain["labelling"] == "clips"
    turns = create_dataset(client, stage_fixture_dataset(tmp_path / "turns", refs="stm"), "Turns")
    assert turns["labelling"] == "turns"
    changed = _ok(client.patch(f"/api/datasets/{turns['id']}", json={"labelling": "clips"}))
    assert changed["labelling"] == "clips"
    assert client.patch(f"/api/datasets/{turns['id']}", json={"labelling": "lanes"}).status_code == 422


def test_whole_clip_run_writes_one_segment_per_clip_and_uses_no_segmenter(clip_replay, client, tmp_path) -> None:
    flow = {"ids": {n: m["catalog_id"] for n, m in register_models(client).items()}}
    ids = flow["ids"]
    ds = _clip_dataset(client, tmp_path)
    pv = _ok(client.get(f"/api/label/datasets/{ds}/preview", params={"asr_model_ids": ",".join(ids[n] for n in ASR_MODELS)}))
    assert pv["seg"] is None
    assert [s["stage"] for s in (pv["plan"] or {"steps": []})["steps"]].count("seg") == 0
    results = run_clips(client, flow, ds)
    assert results["segmenter"] is None
    assert results["run"]["params"]["whole_clip"] is True
    assert results["summary"]["segments"] == results["dataset"]["usable_files"]
    listed = _ok(client.get(f"/api/label/datasets/{ds}/clips"))
    assert len(listed["clips"]) == results["dataset"]["usable_files"]
    for c in listed["clips"]:
        assert c["segments"] == 1
        view = _ok(client.get(f"/api/label/files/{c['id']}"))
        s = view["segments"][0]
        assert (s["start"], s["speaker"]) == (0.0, None)
        assert abs(s["end"] - c["duration"]) < 2e-3
        assert s["text"]
        assert {a["service"] for a in s["alternatives"]} == {ids[n] for n in ASR_MODELS}
    # saved outputs: the second run needs no model, and a segmenter or speakers are refused
    again = _ok(client.get(f"/api/label/datasets/{ds}/preview", params={"asr_model_ids": ",".join(ids[n] for n in ASR_MODELS)}))
    assert again["new_members"] == 0
    assert client.post(f"/api/label/datasets/{ds}/runs", json={"tasks": ["asr", "diar"], "asr_model_ids": [ids[ASR_MODELS[0]]]}).status_code == 422
    assert client.post(f"/api/label/datasets/{ds}/runs", json={"tasks": ["asr"], "seg_model_id": ids["silero-vad"], "asr_model_ids": [ids[ASR_MODELS[0]]]}).status_code == 422


def test_a_whole_clip_run_keeps_any_reviewed_work_on_a_clip(clip_replay, client, tmp_path) -> None:
    flow = {"ids": {n: m["catalog_id"] for n, m in register_models(client).items()}}
    ds = _clip_dataset(client, tmp_path)
    clips = _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]
    typed, short = clips[0], clips[1]
    _ok(client.post(f"/api/label/files/{typed['id']}/segments", json={"start": 0, "end": typed["duration"], "text": "typed by hand"}), 201)
    # a reviewed segment over a small part of the clip still keeps the clip out of the run
    _ok(client.post(f"/api/label/files/{short['id']}/segments", json={"start": 0, "end": 0.5, "text": "a bit"}), 201)
    results = run_clips(client, flow, ds)
    assert results["summary"]["kept_reviewed"] == 2
    after = {c["id"]: c for c in _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]}
    assert after[typed["id"]]["segment"]["text"] == "typed by hand"
    assert after[short["id"]]["segments"] == 1
    assert after[short["id"]]["segment"]["text"] == "a bit"


def test_turn_datasets_still_need_a_segmenter(replay, client, tmp_path) -> None:
    register_models(client)
    ds = audio_only_dataset(client, tmp_path)
    assert _ok(client.get(f"/api/datasets/{ds}"))["labelling"] == "turns"
    assert client.post(f"/api/label/datasets/{ds}/runs", json={"tasks": ["asr"], "asr_model_ids": [ASR_MODELS[0]]}).status_code == 422
    assert client.get(f"/api/label/datasets/{ds}/preview").status_code == 422


def test_clip_list_shows_one_transcript_for_a_clip_of_any_shape(client, tmp_path) -> None:
    ds = _clip_dataset(client, tmp_path)
    clips = _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]
    empty, one, several, gone = clips[:4]
    assert empty["segment"] is None and empty["segments"] == 0
    _ok(client.post(f"/api/label/files/{one['id']}/segments", json={"start": 0, "end": one["duration"], "text": "one"}), 201)
    a = _ok(client.post(f"/api/label/files/{several['id']}/segments", json={"start": 0, "end": 1, "text": "first"}), 201)
    b = _ok(client.post(f"/api/label/files/{several['id']}/segments", json={"start": 1, "end": 2, "text": "second"}), 201)
    c = _ok(client.post(f"/api/label/files/{several['id']}/segments", json={"start": 2, "end": 3, "text": "thrown"}), 201)
    _ok(client.post("/api/label/segments/bulk", json={"ids": [c["id"]], "action": "reject"}))
    d = _ok(client.post(f"/api/label/files/{gone['id']}/segments", json={"start": 0, "end": 1, "text": None}), 201)
    _ok(client.post("/api/label/segments/bulk", json={"ids": [d["id"]], "action": "reject"}))
    after = {x["id"]: x for x in _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]}
    assert after[one["id"]]["segment"]["text"] == "one" and after[one["id"]]["segment"]["created"] is True
    assert after[several["id"]]["segments"] == 2
    assert after[several["id"]]["segment"]["id"] == a["id"] and after[several["id"]]["segment"]["text"] == "first second"
    assert b["id"] != a["id"]
    assert after[gone["id"]]["segments"] == 0 and after[gone["id"]]["segment"]["status"] == "rejected"


def test_confirm_all_high_confidence_across_a_dataset_is_undone_clip_by_clip(client, tmp_path) -> None:
    ds = _clip_dataset(client, tmp_path)
    clips = _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]
    a, b = clips[:2]
    with session_scope() as session:
        for c in (a, b):
            session.add(Segment(audio_file_id=c["id"], start=0.0, end=c["duration"], text="auto", status="auto", confidence=0.9))
    assert _ok(client.post(f"/api/label/datasets/{ds}/confirm-auto"))["confirmed"] == 2
    after = {x["id"]: x for x in _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]}
    assert after[a["id"]]["segment"]["status"] == after[b["id"]]["segment"]["status"] == "confirmed"
    _ok(client.post(f"/api/label/files/{a['id']}/undo"))
    after = {x["id"]: x for x in _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]}
    assert after[a["id"]]["segment"]["status"] == "auto"
    assert after[b["id"]]["segment"]["status"] == "confirmed"


def test_confirm_all_high_confidence_leaves_clips_that_need_a_merge(client, tmp_path) -> None:
    ds = _clip_dataset(client, tmp_path)
    one, several = _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"][:2]
    with session_scope() as session:
        session.add(Segment(audio_file_id=one["id"], start=0.0, end=one["duration"], text="auto", status="auto", confidence=0.9))
        for start in (0.0, 1.0):
            session.add(Segment(audio_file_id=several["id"], start=start, end=start + 1, text="part", status="auto", confidence=0.9))
    assert _ok(client.post(f"/api/label/datasets/{ds}/confirm-auto"))["confirmed"] == 1
    after = {x["id"]: x for x in _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]}
    assert after[one["id"]]["segment"]["status"] == "confirmed"
    assert after[several["id"]]["segment"]["status"] == "auto"


def test_a_confirmed_clip_goes_into_a_reference_package_as_a_transcript(client, tmp_path) -> None:
    ds = _clip_dataset(client, tmp_path)
    c = _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"][0]
    s = _ok(client.post(f"/api/label/files/{c['id']}/segments", json={"start": 0, "end": c["duration"], "text": "hello there"}), 201)
    _ok(client.post("/api/label/segments/bulk", json={"ids": [s["id"]], "action": "confirm"}))
    from concord_label.label import refpkg

    with session_scope() as session:
        sel = refpkg.select_files(session, session.get(Dataset, ds))
    assert sel.label_types == ["asr"] and sel.refs_file == refpkg.REFS_JSONL and not sel.problem


def _uploaded_clips(client, tmp_path, name="Labelled clips") -> dict:
    return create_dataset(client, stage_fixture_dataset(tmp_path / name.replace(" ", "_"), refs="asr-jsonl"), name)


def _clips(client, ds: int) -> list[dict]:
    return _ok(client.get(f"/api/label/datasets/{ds}/clips"))["clips"]


def _segment(client, clip: dict) -> dict:
    return _ok(client.get(f"/api/label/files/{clip['id']}"))["segments"][0]


def test_an_uploaded_label_waits_for_a_person(client, tmp_path) -> None:
    ds = _uploaded_clips(client, tmp_path)
    clips = _clips(client, ds["id"])
    s = _segment(client, clips[0])
    assert s["status"] == "pseudo" and not s["reviewed"] and s["text"] and s["uploaded_text"] == s["text"]
    assert _ok(client.get(f"/api/label/datasets/{ds['id']}/uploaded")) == {"labels": len(clips), "waiting": len(clips)}
    # not a reference yet: it can't be scored or published
    assert _ok(client.get(f"/api/label/datasets/{ds['id']}/live"))["asr"]["confirmed"] == 0
    assert client.post(f"/api/datasets/{ds['id']}/reference-exports", json={"name": "x"}).status_code == 422
    row = next(r for r in _ok(client.get("/api/label/console")) if r["id"] == ds["id"])
    assert row["next"] == {"kind": "review", "label": f"Review {len(clips)}", "status": "pseudo"}


def test_a_whole_clip_run_writes_the_pseudo_label_beside_the_uploaded_one(clip_replay, client, session, tmp_path) -> None:
    flow = {"ids": {n: m["catalog_id"] for n, m in register_models(client).items()}}
    ds = _uploaded_clips(client, tmp_path)
    clips = _clips(client, ds["id"])
    before = {c["id"]: _segment(client, c) for c in clips}
    # what the first clip's label says, so that clip matches; the second gets a label the models won't say
    heard = run_clips(client, flow, _clip_dataset(client, tmp_path / "plain"))
    assert heard["summary"]["segments"]
    plain = next(d for d in _ok(client.get("/api/datasets")) if d["name"] == "Short clips")
    said = {c["stem"]: c["segment"]["text"] for c in _clips(client, plain["id"])}
    match, differ = clips[0], clips[1]
    rows = {s.audio_file_id: s for s in session.scalars(sa.select(Segment).where(Segment.audio_file_id.in_([match["id"], differ["id"]])))}
    for fid, text in ((match["id"], said[match["stem"]].upper() + "!"), (differ["id"], "no model says this")):
        rows[fid].text = text
        rows[fid].meta = {"uploaded": {"text": text, "speaker": None}}
    session.commit()

    first = run_clips(client, flow, ds["id"])["summary"]
    assert first["segments"] == len(clips) and first["kept_reviewed"] == first["kept_uploaded"] == 0
    assert first["uploaded"]["matches"] >= 1 and first["uploaded"]["differs"] >= 1
    assert first["uploaded"]["matches"] + first["uploaded"]["differs"] == len(clips)
    a, b = _segment(client, match), _segment(client, differ)
    assert a["status"] == "auto" and a["text"] == said[match["stem"]] and a["uploaded_text"] == said[match["stem"]].upper() + "!"
    assert b["status"] == "pseudo" and b["text"] == said[differ["stem"]] and b["uploaded_text"] == "no model says this"
    assert a["uid"] == before[match["id"]]["uid"] and a["id"] == before[match["id"]]["id"]  # the same segment
    assert len(_ok(client.get(f"/api/label/files/{differ['id']}"))["segments"]) == 1
    assert {x["service"] for x in b["alternatives"]} == {flow["ids"][n] for n in ASR_MODELS}

    # a run can't reject a clip whose label is there to check, and running again keeps the label
    again = _ok(client.post(f"/api/label/datasets/{ds['id']}/runs", json={
        "tasks": ["asr"], "asr_model_ids": [flow["ids"][n] for n in ASR_MODELS], "high": 1.0, "low": 0.999}), 202)
    jobs.run_pending()
    assert _ok(client.get(f"/api/jobs/{again['job']['id']}"))["status"] == "done"
    b = _segment(client, differ)
    assert b["status"] == "pseudo" and b["uploaded_text"] == "no model says this"
    assert _segment(client, match)["status"] == "auto"

    # the person's choice stands: the uploaded text taken and confirmed is kept by the next run
    _ok(client.patch(f"/api/label/segments/{b['id']}", json={"text": b["uploaded_text"]}))
    _ok(client.post("/api/label/segments/bulk", json={"ids": [b["id"]], "action": "confirm"}))
    third = run_clips(client, flow, ds["id"])["summary"]
    assert third["kept_reviewed"] == 1
    kept = _segment(client, differ)
    assert kept["status"] == "confirmed" and kept["text"] == "no model says this"


def test_a_transcript_is_compared_with_its_uploaded_label_wherever_it_is_listed(client, tmp_path) -> None:
    ds = _uploaded_clips(client, tmp_path)
    clip = _clips(client, ds["id"])[0]

    def listed():
        return next(c for c in _clips(client, ds["id"]) if c["id"] == clip["id"])["segment"]["uploaded_differs"]

    s = _segment(client, clip)
    label = s["uploaded_text"]
    assert s["uploaded_differs"] is False and listed() is False  # the label is still the text
    # case and punctuation don't count, as in a whole-clip run
    assert _ok(client.patch(f"/api/label/segments/{s['id']}", json={"text": label.upper() + "!"}))["uploaded_differs"] is False
    assert listed() is False
    assert _ok(client.patch(f"/api/label/segments/{s['id']}", json={"text": "something else"}))["uploaded_differs"] is True
    assert listed() is True and _segment(client, clip)["uploaded_differs"] is True
    assert _ok(client.patch(f"/api/label/segments/{s['id']}", json={"text": None}))["uploaded_differs"] is None
    assert listed() is None
    # a clip with no label has nothing to compare
    plain = _clip_dataset(client, tmp_path / "plain")
    bare = _clips(client, plain)[0]
    made = _ok(client.post(f"/api/label/files/{bare['id']}/segments", json={"start": 0, "end": 1, "text": "hi"}), 201)
    assert made["uploaded_differs"] is None


def test_a_clip_with_several_segments_lists_its_lowest_confidence_and_no_comparison(client, session, tmp_path) -> None:
    ds = _uploaded_clips(client, tmp_path)
    clip = _clips(client, ds["id"])[0]
    first = _segment(client, clip)
    second = _ok(client.post(f"/api/label/files/{clip['id']}/segments", json={"start": 1, "end": 2, "text": "second"}), 201)
    thrown = _ok(client.post(f"/api/label/files/{clip['id']}/segments", json={"start": 2, "end": 3, "text": "thrown"}), 201)
    _ok(client.post("/api/label/segments/bulk", json={"ids": [thrown["id"]], "action": "reject"}))
    for sid, conf in ((first["id"], 0.9), (second["id"], 0.4), (thrown["id"], 0.1)):
        session.get(Segment, sid).confidence = conf
    session.commit()
    row = next(c for c in _clips(client, ds["id"]) if c["id"] == clip["id"])
    assert row["segments"] == 2 and row["segment"]["confidence"] == 0.4 and row["segment"]["uploaded_differs"] is None


def test_each_file_counts_its_unchecked_segments_by_agreement(client, session, tmp_path) -> None:
    ds = _clip_dataset(client, tmp_path)
    fid = _clips(client, ds)[0]["id"]
    made = [_ok(client.post(f"/api/label/files/{fid}/segments", json={"start": i, "end": i + 1, "text": "x"}), 201)["id"] for i in range(6)]
    rows = {sid: session.get(Segment, sid) for sid in made}
    for sid, conf in zip(made, (0.494, 0.495, 0.9, 0.9, None, 0.9), strict=True):
        rows[sid].confidence = conf
        rows[sid].edited_at = None
    rows[made[3]].status = SegmentStatus.confirmed  # a person checked this one
    rows[made[5]].edited_at = rows[made[5]].updated_at  # and edited this one
    session.commit()
    files = _ok(client.get(f"/api/label/datasets/{ds}/files"))
    assert next(f for f in files if f["id"] == fid)["agreement"] == {"49": 1, "50": 1, "90": 1}
    assert all(f["agreement"] == {} for f in files if f["id"] != fid)


def test_keep_all_uploaded_labels_restores_them_and_undoes_clip_by_clip(clip_replay, client, tmp_path) -> None:
    flow = {"ids": {n: m["catalog_id"] for n, m in register_models(client).items()}}
    ds = _uploaded_clips(client, tmp_path)
    clips = _clips(client, ds["id"])
    labels = {c["id"]: _segment(client, c)["text"] for c in clips}
    run_clips(client, flow, ds["id"])
    pseudo = _segment(client, clips[0])["text"]
    assert pseudo != labels[clips[0]["id"]]
    assert _ok(client.post(f"/api/label/datasets/{ds['id']}/confirm-uploaded"))["confirmed"] == len(clips)
    assert _ok(client.get(f"/api/label/datasets/{ds['id']}/uploaded")) == {"labels": len(clips), "waiting": 0}
    for c in clips:
        s = _segment(client, c)
        assert s["status"] == "confirmed" and s["text"] == labels[c["id"]]
    assert _ok(client.get(f"/api/label/datasets/{ds['id']}/live"))["asr"]["confirmed"] == len(clips)
    assert _ok(client.post(f"/api/datasets/{ds['id']}/reference-exports", json={"name": "x"}), 200, 201, 202)
    _ok(client.post(f"/api/label/files/{clips[0]['id']}/undo"))
    back = _segment(client, clips[0])
    assert back["text"] == pseudo and back["status"] != "confirmed" and back["uploaded_text"] == labels[clips[0]["id"]]


def test_a_run_with_a_segmenter_leaves_uploaded_turns_as_they_are(replay, client, tmp_path) -> None:
    flow = {"ids": {n: m["catalog_id"] for n, m in register_models(client).items()}}
    ds = create_dataset(client, stage_fixture_dataset(tmp_path / "turns", refs="stm"), "Turns")
    files = [f for f in _ok(client.get(f"/api/label/datasets/{ds['id']}/files")) if f["usable"]]
    before = {f["id"]: _ok(client.get(f"/api/label/files/{f['id']}"))["segments"] for f in files}
    started = _ok(client.post(f"/api/label/datasets/{ds['id']}/runs", json={
        "tasks": ["asr", "diar"], "seg_model_id": flow["ids"]["pyannote-diarization"],
        "asr_model_ids": [flow["ids"][ASR_MODELS[0]]]}), 202)
    jobs.run_pending()
    assert _ok(client.get(f"/api/jobs/{started['job']['id']}"))["status"] == "done"
    summary = _ok(client.get(f"/api/label/datasets/{ds['id']}/results"))["summary"]
    assert summary["kept_uploaded"] > 0 and summary["kept_reviewed"] == 0
    for f in files:
        now = {s["id"]: s for s in _ok(client.get(f"/api/label/files/{f['id']}"))["segments"]}
        for old in before[f["id"]]:
            assert now[old["id"]]["text"] == old["text"] and now[old["id"]]["status"] == "pseudo"


def test_a_label_package_carries_an_uploaded_label(client, session, tmp_path) -> None:
    ds = _uploaded_clips(client, tmp_path)
    s = session.scalar(sa.select(Segment).join(AudioFile).where(AudioFile.dataset_id == ds["id"]))
    assert sync.segment_record(s, "sha")["uploaded"] == s.uploaded


def test_migration_starts_uploaded_labels_in_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONCORD_DATA", str(tmp_path / "data"))
    reset_settings()
    db.reset_engines()
    try:
        (tmp_path / "data").mkdir()
        cfg = db.alembic_config()
        command.upgrade(cfg, "label_0009")
        eng = sa.create_engine(db.db_url())
        with eng.begin() as c:
            for sql in (
                """INSERT INTO dataset (id, uid, name, tags, label_types, mode, status, report, created_at, catalog_dataset_id) VALUES
                   (1, 'a', 'uploaded', '[]', '[]', 'annotated', 'ready', '{}', '2026-01-01', NULL),
                   (2, 'b', 'in the catalog', '[]', '[]', 'annotated', 'ready', '{}', '2026-01-01', 'cat')""",
                """INSERT INTO audio_file (id, dataset_id, rel_path, sha256, ext, status, meta) VALUES
                   (1, 1, 'a.wav', 'x', '.wav', 'ok', '{}'), (2, 2, 'a.wav', 'x', '.wav', 'ok', '{}')""",
                """INSERT INTO segment (id, uid, audio_file_id, start, end, speaker, text, status, flagged, meta, updated_at, edited_at) VALUES
                   (1, '1', 1, 0, 1, 'x', 'untouched', 'confirmed', 0, '{}', '2026-01-01', NULL),
                   (2, '2', 1, 1, 2, NULL, 'by a person', 'confirmed', 0, '{}', '2026-01-01', '2026-01-02'),
                   (3, '3', 1, 2, 3, NULL, 'from a package', 'confirmed', 0, '{"imported_from": 1}', '2026-01-01', NULL),
                   (4, '4', 2, 0, 1, NULL, 'published', 'confirmed', 0, '{}', '2026-01-01', NULL)""",
            ):
                c.execute(sa.text(sql))
        command.upgrade(cfg, "label_0010")
        read = "SELECT id, status, json_extract(meta, '$.uploaded.text'), json_extract(meta, '$.uploaded.speaker') FROM segment ORDER BY id"
        with eng.connect() as c:
            assert [tuple(r) for r in c.execute(sa.text(read))] == [
                (1, "pseudo", "untouched", "x"), (2, "confirmed", None, None), (3, "confirmed", None, None),
                (4, "confirmed", None, None)]
            assert c.scalar(sa.text("SELECT text FROM segment WHERE id = 1")) == "untouched"
        # a pseudo-label has taken the text by the time someone goes back
        with eng.begin() as c:
            c.execute(sa.text("UPDATE segment SET text = 'pseudo', confidence = 0.5 WHERE id = 1"))
        command.downgrade(cfg, "label_0009")
        with eng.connect() as c:
            assert tuple(c.execute(sa.text("SELECT status, text, confidence, meta FROM segment WHERE id = 1")).one()) == ("confirmed", "untouched", None, "{}")
        eng.dispose()
    finally:
        db.reset_engines()
        reset_settings()


def test_migration_starts_datasets_from_their_segments(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONCORD_DATA", str(tmp_path / "data"))
    reset_settings()
    db.reset_engines()
    try:
        (tmp_path / "data").mkdir()
        cfg = db.alembic_config()
        command.upgrade(cfg, "label_0008")
        eng = sa.create_engine(db.db_url())
        with eng.begin() as c:
            for sql in (
                """INSERT INTO dataset (id, uid, name, tags, label_types, mode, status, report, created_at) VALUES
                   (1, 'a', 'clips', '[]', '[]', 'annotated', 'ready', '{}', '2026-01-01'),
                   (2, 'b', 'speakers', '[]', '[]', 'annotated', 'ready', '{}', '2026-01-01'),
                   (3, 'c', 'several', '[]', '[]', 'annotated', 'ready', '{}', '2026-01-01'),
                   (4, 'd', 'empty', '[]', '[]', 'audio_only', 'ready', '{}', '2026-01-01')""",
                """INSERT INTO audio_file (id, dataset_id, rel_path, sha256, ext, status, meta) VALUES
                   (1, 1, 'a.wav', 'x', '.wav', 'ok', '{}'), (2, 1, 'b.wav', 'y', '.wav', 'ok', '{}'),
                   (3, 2, 'a.wav', 'x', '.wav', 'ok', '{}'), (4, 3, 'a.wav', 'x', '.wav', 'ok', '{}')""",
                """INSERT INTO segment (uid, audio_file_id, start, end, speaker, text, status, flagged, meta, updated_at) VALUES
                   ('1', 1, 0, 1, NULL, 'a', 'confirmed', 0, '{}', '2026-01-01'),
                   ('2', 2, 0, 1, NULL, 'b', 'pseudo', 0, '{}', '2026-01-01'),
                   ('3', 3, 0, 1, 'x', 'c', 'confirmed', 0, '{}', '2026-01-01'),
                   ('4', 4, 0, 1, NULL, 'd', 'confirmed', 0, '{}', '2026-01-01'),
                   ('5', 4, 1, 2, NULL, 'e', 'confirmed', 0, '{}', '2026-01-01')""",
            ):
                c.execute(sa.text(sql))
        command.upgrade(cfg, "label_0009")
        with eng.connect() as c:
            got = {name: labelling for name, labelling in c.execute(sa.text("SELECT name, labelling FROM dataset"))}
        eng.dispose()
        assert got == {"clips": "clips", "speakers": "turns", "several": "turns", "empty": "turns"}
    finally:
        db.reset_engines()
        reset_settings()
