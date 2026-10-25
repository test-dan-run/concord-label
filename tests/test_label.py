"""Label on recorded responses: models from the (replay) catalog, pseudo-labelling an audio-only copy of the
fixture set, the console, annotation edits and live evaluation (HTTP API, no GPU)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from concord_label.core.models import Segment, SegmentEdit
from label_helpers import ASR_MODELS, SEG_MODELS, _ok, label


def test_models_and_capabilities_come_from_the_catalog(label_flow):
    assert set(label_flow["models"]) == set(ASR_MODELS + SEG_MODELS)
    caps = {n: (m["capabilities"], m["timing_level"]) for n, m in label_flow["models"].items()}
    assert caps["faster-whisper-large-v3"] == (["asr"], "word")
    assert caps["whisper-large-v3"] == (["asr"], "segment")
    assert caps["silero-vad"] == (["vad"], "segment")
    assert caps["pyannote-diarization"] == (["diar"], "segment")
    assert caps["nemotron-3-diarization"] == (["diar"], "segment")


def _preview(client, flow):
    ids = flow["ids"]
    asr = f"{ids['whisper-large-v3']},{ids['faster-whisper-large-v3']}"
    return _ok(client.get(f"/api/label/datasets/{flow['unlabelled']}/preview?seg_model_id={ids['pyannote-diarization']}"
                          f"&asr_model_ids={asr}"))


def test_preview_counts_saved_outputs(label_flow, client):
    pv = _preview(client, label_flow)
    # nothing is in the cache yet: the segmenter and both members need inference
    assert pv["seg"]["state"] == "new" and pv["files"] == 6
    assert [m["state"] for m in pv["members"]] == ["new", "new"]
    assert pv["saved_members"] == 0 and pv["new_members"] == 2
    started, _ = label(client, label_flow)
    assert started["job"]["required_services"] == sorted(["pyannote-diarization", *ASR_MODELS])
    # the run saved every output it used, so the same choice again costs nothing
    pv = _preview(client, label_flow)
    assert pv["seg"]["state"] == "saved"
    assert [m["state"] for m in pv["members"]] == ["saved", "saved"]
    assert pv["saved_members"] == 2 and pv["new_members"] == 0


def test_refuse_needs_no_services(labelled, client):
    started, _ = label(client, labelled, strategy="rover")
    assert started["job"]["required_services"] == []


def test_label_run_writes_triaged_segments(labelled, client, session):
    res = labelled["results"]
    runs = _ok(client.get(f"/api/label/datasets/{labelled['unlabelled']}/runs"))
    assert [r["id"] for r in runs] == [res["run"]["id"]] and runs[0]["status"] == "done"
    s = res["summary"]
    assert s["segments"] > 0 and s["files"] == 6 and s["kept_reviewed"] == 0
    assert sum(s["triage"].values()) == s["segments"]
    assert {c["member"] for c in s["chosen"]} <= {"whisper-large-v3", "faster-whisper-large-v3"}
    assert len(s["agreement"]["cells"]) == 2
    segs = list(session.scalars(select(Segment).where(Segment.source_run_id == res["run"]["id"])))
    assert len(segs) == s["segments"]
    assert all(x.speaker and x.text is not None and x.edited_at is None for x in segs)
    for x in segs:
        expect = "auto" if x.confidence >= 0.85 else ("pseudo" if x.confidence >= 0.5 else "rejected")
        assert x.status == expect
        assert set(x.meta["members"]) == {"whisper-large-v3", "faster-whisper-large-v3"}
    q = res["queue"]
    assert all(r["status"] in ("pseudo", "rejected") for r in q)
    assert [r["confidence"] for r in q] == sorted(r["confidence"] for r in q)


def test_console_progress_adds_up(labelled, client):
    rows = {r["id"]: r for r in _ok(client.get("/api/label/console"))}
    r = rows[labelled["unlabelled"]]
    assert r["label_source"] == "pseudo-labels" and r["tasks"] == ["asr", "diar"]
    bar = r["progress"]["bar"]
    assert bar["unlabelled"] == 0 and bar["running"] == 0
    assert sum(bar.values()) == pytest.approx(r["hours"] * 3600, rel=1e-6)
    assert r["next"]["kind"] == "review" or r["next"]["label"].startswith("Confirm")
    ref = rows[labelled["dataset"]["id"]]
    assert ref["label_source"] == "uploaded references" and ref["progress"]["bar"]["confirmed"] > 0


def test_rerun_keeps_reviewed_work(labelled, client, session):
    ds = labelled["unlabelled"]
    files = _ok(client.get(f"/api/label/datasets/{ds}/files"))
    view = _ok(client.get(f"/api/label/files/{files[0]['id']}"))
    first = view["segments"][0]
    _ok(client.patch(f"/api/label/segments/{first['id']}", json={"text": "Edited by hand.", "status": "confirmed"}))
    # re-fuse with ROVER: no inference, the edited segment survives, the rest is replaced
    _, res = label(client, labelled, strategy="rover")
    s = res["summary"]
    assert s["kept_reviewed"] >= 1 and s["replaced"] > 0
    assert s["strategy_applied"] == {"best_aligned": s["segments"]}  # only one word-timed member
    kept = session.get(Segment, first["id"])
    assert kept.text == "Edited by hand." and kept.status == "confirmed" and kept.reviewed


def test_annotate_edits(labelled, client):
    ds = labelled["unlabelled"]
    fid = _ok(client.get(f"/api/label/datasets/{ds}/files"))[0]["id"]
    v = _ok(client.get(f"/api/label/files/{fid}"))
    assert v["position"]["index"] == 1 and v["next_file_id"] and v["keywords"] == []
    assert v["diariser"] == "pyannote-diarization"  # the run's segmenter (label_helpers.label)
    segs = v["segments"]
    assert not any(x["created"] for x in segs)
    assert len(segs) >= 3 and all(len(x["alternatives"]) == 2 for x in segs)
    a, b, c = segs[0], segs[1], segs[2]
    # bulk: assign speaker + confirm
    out = _ok(client.post("/api/label/segments/bulk", json={"ids": [a["id"], b["id"]], "action": "assign_speaker",
                                                             "speaker": "agent"}))
    assert {x["speaker"] for x in out["segments"]} == {"agent"} and all(x["reviewed"] for x in out["segments"])
    _ok(client.post("/api/label/segments/bulk", json={"ids": [a["id"]], "action": "confirm"}))
    # merge a + b
    m = _ok(client.post("/api/label/segments/merge", json={"ids": [a["id"], b["id"]]}))
    assert m["id"] == a["id"] and m["end"] == b["end"] and m["status"] == "pseudo"
    assert m["text"].startswith(a["text"].strip()[:10])
    # split c at its middle: both halves to review, text kept in order
    mid = round((c["start"] + c["end"]) / 2, 3)
    h1, h2 = _ok(client.post(f"/api/label/segments/{c['id']}/split", json={"t": mid}))
    assert h1["end"] == h2["start"] == mid and h1["status"] == h2["status"] == "pseudo"
    assert f"{h1['text']} {h2['text']}".split() == c["text"].split()
    assert _ok(client.post(f"/api/label/segments/{c['id']}/split", json={"t": c["start"]}), 422)
    # bad span, create, delete
    _ok(client.patch(f"/api/label/segments/{h1['id']}", json={"end": 9999}), 422)
    new = _ok(client.post(f"/api/label/files/{fid}/segments", json={"start": 0.0, "end": 0.4, "speaker": "agent"}), 201)
    assert new["status"] == "pseudo" and new["reviewed"] and new["created"]
    # a drag on the speaker lanes: moved, trimmed and given to another speaker in one patch
    moved = _ok(client.patch(f"/api/label/segments/{new['id']}", json={"start": 0.1234, "end": 0.6, "speaker": "spk7"}))
    assert (moved["start"], moved["end"], moved["speaker"], moved["created"]) == (0.123, 0.6, "spk7", True)
    _ok(client.delete(f"/api/label/segments/{new['id']}"), 204)
    # confirm all high-confidence
    n = _ok(client.post(f"/api/label/files/{fid}/confirm-auto"))["confirmed"]
    v2 = _ok(client.get(f"/api/label/files/{fid}"))
    assert n == sum(1 for x in segs[3:] if x["status"] == "auto")
    assert not any(x["status"] == "auto" for x in v2["segments"])


def _segs(client, fid):
    """The file's segments as Annotate sees them, apart from `updated_at` (an undo renews it)."""
    return [{k: v for k, v in x.items() if k != "updated_at"} for x in _ok(client.get(f"/api/label/files/{fid}"))["segments"]]


def _history(client, fid):
    return _ok(client.get(f"/api/label/files/{fid}"))["history"]


def test_undo_and_redo_each_kind_of_edit(labelled, client, session):
    ds = labelled["unlabelled"]
    fid = _ok(client.get(f"/api/label/datasets/{ds}/files"))[0]["id"]
    assert _history(client, fid) == {"undo": None, "redo": None}
    segs = _segs(client, fid)
    a, b, c = segs[0], segs[1], segs[2]
    c_updated = session.get(Segment, c["id"]).updated_at
    edits = [
        ("text edit", lambda: client.patch(f"/api/label/segments/{a['id']}", json={"text": "Changed."})),
        ("confirm", lambda: client.post("/api/label/segments/bulk", json={"ids": [a["id"], b["id"]], "action": "confirm"})),
        ("merge", lambda: client.post("/api/label/segments/merge", json={"ids": [a["id"], b["id"]]})),
        ("split", lambda: client.post(f"/api/label/segments/{c['id']}/split", json={"t": round((c["start"] + c["end"]) / 2, 3)})),
        ("new segment", lambda: client.post(f"/api/label/files/{fid}/segments", json={"start": 0.0, "end": 0.4, "speaker": "x"})),
        ("delete", lambda: client.delete(f"/api/label/segments/{c['id']}")),
        ("confirm all high-confidence", lambda: client.post(f"/api/label/files/{fid}/confirm-auto")),
        ("speaker change", lambda: client.post("/api/label/segments/bulk", json={"ids": [b["id"]], "action": "assign_speaker",
                                                                               "speaker": "agent"})),
        ("move", lambda: client.patch(f"/api/label/segments/{b['id']}", json={"start": b["start"] + 0.1, "end": b["end"] + 0.1,
                                                                             "speaker": "spk7"})),
    ]
    for action, do in edits:
        before = _segs(client, fid)
        assert do().status_code < 300
        after = _segs(client, fid)
        assert after != before and _history(client, fid)["undo"] == action
        undone = _ok(client.post(f"/api/label/files/{fid}/undo"))
        assert undone["action"] == action
        assert _segs(client, fid) == before, action  # ids and uids come back too
        assert _history(client, fid)["redo"] == action
        redone = _ok(client.post(f"/api/label/files/{fid}/redo"))
        assert redone["action"] == action and _segs(client, fid) == after, action
        _ok(client.post(f"/api/label/files/{fid}/undo"))
    # every edit is undone again, so the file is as the run left it, and a restored segment has a new updated_at
    assert _segs(client, fid) == segs
    session.expire_all()
    assert session.get(Segment, c["id"]).updated_at > c_updated
    # each new edit dropped the edits undone before it, so only the last one can be redone
    assert _ok(client.post(f"/api/label/files/{fid}/undo"), 409)["detail"] == "There is nothing to undo"
    assert _history(client, fid) == {"undo": None, "redo": "move"}


def test_a_new_edit_clears_redo_and_the_history_keeps_200(labelled, client, session):
    ds = labelled["unlabelled"]
    fid = _ok(client.get(f"/api/label/datasets/{ds}/files"))[0]["id"]
    a = _segs(client, fid)[0]
    _ok(client.patch(f"/api/label/segments/{a['id']}", json={"text": "One."}))
    _ok(client.post(f"/api/label/files/{fid}/undo"))
    assert _history(client, fid) == {"undo": None, "redo": "text edit"}
    _ok(client.post("/api/label/segments/bulk", json={"ids": [a["id"]], "action": "flag"}))
    assert _history(client, fid) == {"undo": "flag", "redo": None}
    assert _ok(client.post(f"/api/label/files/{fid}/redo"), 409)["detail"] == "There is nothing to redo"
    # an edit that changes nothing is not recorded (flagging a flagged segment again only renews edited_at)
    _ok(client.post("/api/label/segments/bulk", json={"ids": [a["id"]], "action": "flag"}))
    assert session.query(SegmentEdit).filter_by(audio_file_id=fid).count() == 1
    for k in range(205):
        _ok(client.patch(f"/api/label/segments/{a['id']}", json={"flagged": k % 2 == 0}))
    rows = session.query(SegmentEdit).filter_by(audio_file_id=fid).order_by(SegmentEdit.id).all()
    assert len(rows) == 200 and rows[0].action in ("flag", "unflag")


def test_a_pseudo_label_run_clears_the_history(labelled, client, session):
    ds = labelled["unlabelled"]
    fid = _ok(client.get(f"/api/label/datasets/{ds}/files"))[0]["id"]
    a, b = _segs(client, fid)[:2]
    _ok(client.patch(f"/api/label/segments/{a['id']}", json={"text": "Kept by hand."}))
    assert _history(client, fid)["undo"] == "text edit"
    label(client, labelled, strategy="rover")
    assert _history(client, fid) == {"undo": None, "redo": None}
    assert _ok(client.post(f"/api/label/files/{fid}/undo"), 409)["detail"] == "There is nothing to undo"
    # a change made around the history (here straight in the database) turns the undo into a 409, and the
    # history is forgotten
    _ok(client.patch(f"/api/label/segments/{a['id']}", json={"text": "Edited again."}))
    session.get(Segment, a["id"]).text = "Changed elsewhere."
    session.commit()
    r = _ok(client.post(f"/api/label/files/{fid}/undo"), 409)
    assert r["detail"] == "This file changed since that edit, so it can't be undone"
    assert _history(client, fid) == {"undo": None, "redo": None}
    assert session.get(Segment, a["id"]).text == "Changed elsewhere."


def test_shared_boundary_and_speaker_rename(labelled, client):
    ds = labelled["unlabelled"]
    fid = _ok(client.get(f"/api/label/datasets/{ds}/files"))[0]["id"]
    segs = _segs(client, fid)
    # two segments that touch: the first ends where the second starts (moved there by hand)
    a, b, c = segs[0], segs[1], segs[2]
    _ok(client.patch(f"/api/label/segments/{a['id']}", json={"end": b["start"] - 0.01}))
    before = _segs(client, fid)
    t = round(b["start"] + 0.2, 3)
    out = _ok(client.post("/api/label/segments/boundary", json={"ids": [b["id"], a["id"]], "t": t}))
    assert {(x["id"], x["start"], x["end"]) for x in out} == {(a["id"], a["start"], t), (b["id"], t, b["end"])}
    assert _history(client, fid)["undo"] == "boundary move"
    _ok(client.post(f"/api/label/files/{fid}/undo"))
    assert _segs(client, fid) == before
    # segments that don't touch, a boundary past the end of a segment, and one segment alone are refused
    _ok(client.post("/api/label/segments/boundary", json={"ids": [a["id"], c["id"]], "t": t}), 422)
    _ok(client.post("/api/label/segments/boundary", json={"ids": [a["id"], b["id"]], "t": b["end"]}), 422)
    _ok(client.post("/api/label/segments/boundary", json={"ids": [a["id"]], "t": t}), 422)

    # renaming a speaker gives every one of its segments the new name, as one edit
    old = a["speaker"]
    ids = [x["id"] for x in before if x["speaker"] == old]
    out = _ok(client.post("/api/label/segments/bulk", json={"ids": ids, "action": "rename_speaker", "speaker": "Agent"}))
    assert out["updated"] == len(ids) and {x["speaker"] for x in out["segments"]} == {"Agent"}
    v = _ok(client.get(f"/api/label/files/{fid}"))
    assert old not in v["speakers"] and "Agent" in v["speakers"] and v["history"]["undo"] == "speaker rename"
    _ok(client.post(f"/api/label/files/{fid}/undo"))
    assert _segs(client, fid) == before
    _ok(client.post("/api/label/segments/bulk", json={"ids": ids, "action": "rename_speaker", "speaker": " "}), 422)


def test_live_eval(labelled, client):
    ds = labelled["unlabelled"]
    lv = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    assert lv["asr"]["confirmed"] == 0 and lv["asr"]["models"] == [] and lv["kws"] == {"available": False}
    # cpWER needs fully reviewed files, and it uses the speakers of the run's diariser
    cp = lv["cpwer"]
    assert cp["files"] == 0 and cp["of"] == 6 and cp["diariser"] == "pyannote-diarization" and cp["models"] == []
    assert "every segment is reviewed" in cp["reason"]
    # confirm every segment of every file
    for f in _ok(client.get(f"/api/label/datasets/{ds}/files")):
        ids = [x["id"] for x in _ok(client.get(f"/api/label/files/{f['id']}"))["segments"]]
        if ids:
            _ok(client.post("/api/label/segments/bulk", json={"ids": ids, "action": "confirm"}))
    lv = _ok(client.get(f"/api/label/datasets/{ds}/live"))
    a = lv["asr"]
    assert a["confirmed"] == labelled["results"]["summary"]["segments"]
    names = {m["service"]: m for m in a["models"]}
    assert set(names) == {"whisper-large-v3", "faster-whisper-large-v3"}
    for m in a["models"]:
        assert m["coverage"] == a["confirmed"] and m["curve"][-1]["confirmed"] == a["confirmed"]
        assert m["curve"][-1]["wer"] == pytest.approx(m["wer"])
        if a["ranked"]:
            lo, hi = m["ci"]
            assert lo <= m["wer"] <= hi
    # confirmed text is each segment's chosen member output, so at least one model has a low WER
    assert min(m["wer"] for m in a["models"]) < 0.5
    assert lv["der"]["files"] == 6 and {m["name"] for m in lv["der"]["models"]} >= {"pyannote-diarization"}
    cp = lv["cpwer"]
    assert cp["files"] == 6 and cp["reason"] is None
    assert {m["service"] for m in cp["models"]} == {"whisper-large-v3", "faster-whisper-large-v3"}
    assert all(m["files"] == 6 and m["words"] > 0 for m in cp["models"])
    # the confirmed text is a member's output and the speakers are the diariser's, so the best model is close
    assert cp["models"][0]["cpwer"] < 0.5
    # giving every segment of a file to one speaker puts that file's other speaker's words on the wrong speaker
    f0 = _ok(client.get(f"/api/label/datasets/{ds}/files"))[0]["id"]
    ids = [x["id"] for x in _ok(client.get(f"/api/label/files/{f0}"))["segments"]]
    _ok(client.post("/api/label/segments/bulk", json={"ids": ids, "action": "assign_speaker", "speaker": "solo"}))
    worse = {m["service"]: m["cpwer"] for m in _ok(client.get(f"/api/label/datasets/{ds}/live"))["cpwer"]["models"]}
    assert all(worse[m["service"]] > m["cpwer"] for m in cp["models"])
    # the references are pyannote's own turns after cut merging, so it is best and nearly perfect
    assert lv["der"]["models"][0]["name"] == "pyannote-diarization" and lv["der"]["models"][0]["der"] < 0.1
    assert lv["feed"] and lv["feed"][0]["confirmed_at"]
    # the locked benchmark score comes from Concord Bench once the apps are linked (S5); until then none
    assert all(m["locked"] is None for m in a["models"])
    # Promote is gone: Send to Bench replaces it (S4)
    assert client.post(f"/api/label/datasets/{ds}/promote", json={"name": "x"}).status_code in (404, 405)


def test_label_run_validation(label_flow, client):
    flow = label_flow
    ids, ds = flow["ids"], flow["unlabelled"]
    url = f"/api/label/datasets/{ds}/runs"
    _ok(client.post(url, json={"seg_model_id": ids["silero-vad"], "asr_model_ids": [ids["whisper-large-v3"]]}), 422)
    _ok(client.post(url, json={"seg_model_id": ids["pyannote-diarization"], "asr_model_ids": []}), 422)
    _ok(client.post(url, json={"seg_model_id": ids["pyannote-diarization"], "tasks": ["kws"]}), 422)
    _ok(client.post(url, json={"seg_model_id": ids["pyannote-diarization"], "asr_model_ids": [ids["whisper-large-v3"]],
                               "strategy": "single"}), 422)
    # transcripts only, with a speech-only segmenter, is fine
    _ok(client.post(url, json={"tasks": ["asr"], "seg_model_id": ids["silero-vad"],
                               "asr_model_ids": [ids["whisper-large-v3"]]}), 202)
    _ok(client.post(url, json={"tasks": ["asr"], "seg_model_id": ids["silero-vad"],
                               "asr_model_ids": [ids["whisper-large-v3"]]}), 409)  # one active run per dataset
