"""The Model Catalog as Label uses it, on aicore's FakeCatalog (no catalog, no GPU): models listed from it,
one lease per pipeline stage, waiting in its queue, blocked jobs, the output index (outputs Bench holds are
copied, not computed; nothing is indexed without a build key), POST /api/outputs/batch and the app's
registration at startup."""

from __future__ import annotations

import time

import httpx
import pytest
from aicore.catalog import Catalog, FakeCatalog, LeaseFailed, index_key

from concord_label.core import paths
from concord_label.core.models import Job, ModelOutput, Run, Segment
from concord_label.platform import catalog
from concord_label.runs import cache, jobs
from label_helpers import ASR_MODELS, _ok, label

SEG = "pyannote-diarization"


def rec(model_id: str, tasks: list[str], *, timing: str | None = None, contract: str = "openai",
        api: str = "audio.transcriptions", state: str = "stopped", build_key: str | None = "0123456789abcdef",
        status: str = "verified", usable: str | None = "asr", cold: float = 74.2) -> dict:
    return {"id": model_id, "display_name": model_id.title(), "tags": ["call-centre"], "contract": contract, "api": api,
            "state": state, "build_key": build_key, "image": f"img/{model_id}:1", "cold_start_s": cold,
            "cold_start_measured": True,
            "capabilities": {"build_key": build_key, "status": status, "usable_as": usable, "tasks": tasks,
                             "timing_level": timing}}


def leases(cat: FakeCatalog) -> list[tuple[str, tuple]]:
    return [(n, a) for n, a in cat.calls if n in ("lease", "release")]


def down_catalog() -> Catalog:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return Catalog("http://catalog.test", http=httpx.Client(base_url="http://catalog.test",
                                                           transport=httpx.MockTransport(refuse)))


def preview(client, flow) -> dict:
    ids = flow["ids"]
    return _ok(client.get(f"/api/label/datasets/{flow['unlabelled']}/preview?seg_model_id={ids[SEG]}"
                          f"&asr_model_ids={','.join(ids[n] for n in ASR_MODELS)}"))


# ---------------------------------------------------------------- models

def test_models_are_listed_from_the_catalog(client, fake_catalog, monkeypatch):
    from concord_label.core.settings import reset_settings

    monkeypatch.setenv("CATALOG_UI_URL", "http://catalog.example:8700/ui")
    reset_settings()
    fake_catalog.add_model(rec("whisper-large-v3", ["asr"], timing="word", state="running"))
    fake_catalog.add_model(rec("silero-vad", ["vad"], timing="segment", contract="oip-v2", api="infer",
                               usable="segmenter", cold=8.0))
    fake_catalog.add_model(rec("qwen3-8b", [], contract="openai", api="chat.completions", usable="chat-audio",
                               status="failed"))
    fake_catalog.add_model(rec("bge-m3", [], api="embeddings", usable=None, status="verified"))
    fake_catalog.add_model(rec("sepformer", ["enhance"], contract="oip-v2", api="infer", usable="enhancer"))
    fake_catalog.add_model(rec("sortformer-asr", ["asr", "diar"], contract="oip-v2", api="infer", usable="segmenter"))
    fake_catalog.add_model({**rec("parakeet", []), "capabilities": None})  # not verified yet
    out = _ok(client.get("/api/models"))
    assert out["catalog"]["reachable"] and out["catalog"]["ui_url"] == "http://catalog.example:8700/ui"
    by = {m["catalog_id"]: m for m in out["models"]}
    w = by["whisper-large-v3"]
    assert (w["stages"], w["capabilities"], w["timing_level"], w["state"]) == (["asr"], ["asr"], "word", "running")
    assert w["id"] is None and w["cold_start_s"] == 74.2 and w["build_key"] == "0123456789abcdef"
    assert w["catalog_url"] == "http://catalog.example:8700/ui/models/whisper-large-v3"
    assert by["silero-vad"]["stages"] == ["seg"] and by["silero-vad"]["not_usable"] is None
    assert by["qwen3-8b"]["stages"] == [] and "failed" in by["qwen3-8b"]["not_usable"]
    assert by["bge-m3"]["stages"] == []
    # groups come from every task the verifier found: enhancers too, a joint model in both stage groups,
    # none for a failed, unverified or task-less record
    assert {k: by[k]["groups"] for k in by} == {
        "whisper-large-v3": ["asr"], "silero-vad": ["seg"], "qwen3-8b": [], "bge-m3": [], "sepformer": ["enhance"],
        "sortformer-asr": ["asr", "seg"], "parakeet": []}
    assert by["sepformer"]["not_usable"] == "speech enhancement: not a task Label runs"
    assert [m["catalog_id"] for m in out["models"]][:3] == ["silero-vad", "sortformer-asr", "whisper-large-v3"]
    # no registration endpoints: registering happens in the catalog's own UI
    assert client.post("/api/models", json={"registry_name": "x"}).status_code in (404, 405)
    for path in ("/api/registry", "/api/registry/status", "/api/models/1"):
        assert client.get(path).status_code == 404, path


def test_models_fall_back_to_rows_when_the_catalog_is_down(labelled, client):
    catalog.set_catalog(down_catalog())
    out = _ok(client.get("/api/models"))
    assert out["catalog"]["reachable"] is False and "did not answer" in out["catalog"]["error"]
    by = {m["catalog_id"]: m for m in out["models"]}
    assert set(by) == {SEG, *ASR_MODELS}  # the models label runs used
    assert by[SEG]["state"] == "unknown" and by[SEG]["stages"] == ["seg"] and by[SEG]["id"]
    assert by["faster-whisper-large-v3"]["timing_level"] == "word"


# ---------------------------------------------------------------- leases and the queue

def test_label_run_leases_one_model_per_stage(label_flow, client, replay_catalog):
    replay_catalog.calls.clear()
    started, _ = label(client, label_flow)
    holder = f"label:job-{started['job']['id']}"
    got = leases(replay_catalog)
    assert [n for n, _ in got] == ["lease", "release"] * 3  # each released before the next is taken
    taken = [a for n, a in got if n == "lease"]
    assert taken[0] == (holder, (SEG,))  # stage 1: the segmenter alone, over every file
    assert sorted(a[1] for a in taken[1:]) == [(n,) for n in sorted(ASR_MODELS)]  # stage 2: one member at a time
    assert all(a[0] == holder for a in taken)
    assert any(n == "plan" for n, _ in replay_catalog.calls)
    assert any(n == "index_record" for n, _ in replay_catalog.calls)
    # a re-run with everything saved takes no lease at all
    replay_catalog.calls.clear()
    label(client, label_flow, strategy="rover")
    assert leases(replay_catalog) == []


def test_outputs_carry_the_build_key_and_are_indexed(labelled, session, replay_catalog):
    run = session.get(Run, labelled["results"]["run"]["id"])
    assert run.image_tags[SEG]["build_key"] == replay_catalog.model(SEG)["build_key"]
    rows = session.query(ModelOutput).all()
    assert rows and all(r.build_key == replay_catalog.model(r.model_name)["build_key"] for r in rows)
    assert all("image" not in r.params for r in rows)
    keys = [index_key(r.build_key, r.audio_sha256, r.span_start, r.span_end, r.params_hash) for r in rows]
    hits = replay_catalog.index_lookup("bench", keys)
    assert all(h and h["owner"] == "label" for h in hits)
    assert sorted(h["output_id"] for h in hits) == sorted(r.id for r in rows)


def test_waiting_job_shows_its_queue_position(label_flow, client, replay_catalog, monkeypatch):
    seen = []
    granted = replay_catalog.lease

    def queued_then_granted(holder, models, on_wait=None, **kw):
        if on_wait and not seen:
            on_wait({"id": "l-q", "holder": holder, "models": list(models), "state": "queued", "position": 2,
                     "waiting_for": f"{models[0]} does not fit next to qwen3-32b-awq (leased by bench:job-12)"})
            job_id = int(holder.rsplit("-", 1)[1])
            seen.append(_ok(client.get(f"/api/jobs/{job_id}")))
            seen.append(_ok(client.get("/api/jobs?active=true")))
        return granted(holder, models)

    monkeypatch.setattr(replay_catalog, "lease", queued_then_granted)
    started, _ = label(client, label_flow)
    job, active = seen
    assert job["status"] == "waiting" and job["waiting"]["position"] == 2
    assert job["waiting"]["models"] == [SEG] and "does not fit" in job["waiting"]["waiting_for"]
    assert [j["id"] for j in active] == [started["job"]["id"]]  # waiting counts as active
    done = _ok(client.get(f"/api/jobs/{started['job']['id']}"))
    assert done["status"] == "done" and done["waiting"] is None
    assert any("waiting for pyannote-diarization (#2 in the queue)" in line for line in done["log"])


def test_failed_lease_blocks_the_job(label_flow, client, replay_catalog, monkeypatch, session):
    def fail(holder, models, **kw):
        raise LeaseFailed(0, f"lease l-9 for {', '.join(models)} failed: weights not found at /assets/x")

    monkeypatch.setattr(replay_catalog, "lease", fail)
    ids = label_flow["ids"]
    started = _ok(client.post(f"/api/label/datasets/{label_flow['unlabelled']}/runs",
                              json={"seg_model_id": ids[SEG], "asr_model_ids": [ids[ASR_MODELS[0]]]}), 202)
    jobs.run_pending()
    job = _ok(client.get(f"/api/jobs/{started['job']['id']}"))
    assert job["status"] == "blocked" and "weights not found" in job["error"]
    assert any(line.endswith("weights not found at /assets/x") and "blocked:" in line for line in job["log"])
    run = session.get(Run, started["run"]["id"])
    assert run.status == "blocked"
    # retried once the catalog can start it
    monkeypatch.undo()
    _ok(client.post(f"/api/jobs/{job['id']}/retry"))
    jobs.run_pending()
    assert _ok(client.get(f"/api/jobs/{job['id']}"))["status"] == "done"


def _start(client, flow):
    ids = flow["ids"]
    return _ok(client.post(f"/api/label/datasets/{flow['unlabelled']}/runs",
                           json={"seg_model_id": ids[SEG], "asr_model_ids": [ids[ASR_MODELS[0]]]}), 202)


def test_cancel_a_queued_label_job(label_flow, client, session):
    started = _start(client, label_flow)
    job = _ok(client.post(f"/api/jobs/{started['job']['id']}/cancel"))
    assert job["status"] == "cancelled" and not job["cancelling"]
    assert jobs.run_pending() == 0
    assert session.get(Run, started["run"]["id"]).status == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
    # a new run may start, and the cancelled one can be retried
    _ok(client.post(f"/api/jobs/{job['id']}/retry"))
    jobs.run_pending()
    assert _ok(client.get(f"/api/jobs/{job['id']}"))["status"] == "done"


def test_cancel_a_running_label_job_before_its_next_cut(label_flow, client, replay_catalog, monkeypatch, session):
    seen = []
    granted = replay_catalog.lease

    def cancel_once_granted(holder, models, **kw):
        lease = granted(holder, models)
        if not seen:
            job_id = int(holder.rsplit("-", 1)[1])
            seen.append(_ok(client.post(f"/api/jobs/{job_id}/cancel")))
        return lease

    monkeypatch.setattr(replay_catalog, "lease", cancel_once_granted)
    started = _start(client, label_flow)
    jobs.run_pending()
    assert seen[0]["status"] == "running" and seen[0]["cancelling"]
    job = _ok(client.get(f"/api/jobs/{started['job']['id']}"))
    assert job["status"] == "cancelled" and not job["cancelling"] and job["error"] is None
    assert any("cancelled" in line for line in job["log"])
    run = session.get(Run, started["run"]["id"])
    assert run.status == "cancelled" and not session.query(Segment).filter_by(source_run_id=run.id).count()
    assert [n for n, _ in leases(replay_catalog)] == ["lease", "release"]  # the segmenter's lease, given back
    monkeypatch.undo()
    _ok(client.post(f"/api/jobs/{job['id']}/retry"))
    jobs.run_pending()
    assert _ok(client.get(f"/api/jobs/{job['id']}"))["status"] == "done"


def test_cancel_a_waiting_label_job_releases_its_queued_lease(label_flow, client, replay_catalog, monkeypatch):
    def queued(holder, models, on_wait=None, **kw):
        lease = replay_catalog.request_lease(holder, models)
        lease.update(state="queued", position=3, waiting_for="no GPU free")
        on_wait(lease)
        _ok(client.post(f"/api/jobs/{int(holder.rsplit('-', 1)[1])}/cancel"))
        assert replay_catalog.get_lease(lease["id"])["state"] == "released"  # Lease.wait would see this
        raise LeaseFailed(0, f"lease {lease['id']} for {', '.join(models)} released: no GPU free")

    monkeypatch.setattr(replay_catalog, "lease", queued)
    started = _start(client, label_flow)
    jobs.run_pending()
    job = _ok(client.get(f"/api/jobs/{started['job']['id']}"))
    assert job["status"] == "cancelled" and job["error"] is None


def test_only_label_jobs_can_be_cancelled(client, session):
    j = jobs.enqueue(session, "ingest", {}, [])
    session.commit()
    r = client.post(f"/api/jobs/{j.id}/cancel")
    assert r.status_code == 409 and "pseudo-label" in r.json()["detail"]


def test_catalog_down_blocks_new_work_but_refusing_needs_none(labelled, client, session):
    flow = labelled
    ds = flow["unlabelled"]
    _ok(client.get(f"/api/label/datasets/{ds}/preview?seg_model_id=nemotron-3-diarization"))  # a row for it
    catalog.set_catalog(down_catalog())
    # everything is saved: a re-fuse needs no model, so no catalog
    started, _ = label(client, flow, strategy="rover")
    assert started["job"]["required_services"] == []
    # new work (nemotron never segmented these files) needs a lease: blocked with a clear message
    started = _ok(client.post(f"/api/label/datasets/{ds}/runs", json={
        "seg_model_id": "nemotron-3-diarization", "asr_model_ids": [flow["ids"][ASR_MODELS[0]]]}), 202)
    assert started["job"]["required_services"] == ["nemotron-3-diarization", ASR_MODELS[0]]  # cuts unknown yet
    jobs.run_pending()
    job = _ok(client.get(f"/api/jobs/{started['job']['id']}"))
    assert job["status"] == "blocked" and "did not answer" in job["error"]
    # a model Label never used cannot be resolved while the catalog is down
    r = client.get(f"/api/label/datasets/{ds}/preview?seg_model_id=silero-vad")
    assert r.status_code == 503


def test_claim_prefers_jobs_whose_models_are_running(session, fake_catalog):
    fake_catalog.add_model(rec("big", ["asr"], state="stopped"))
    fake_catalog.add_model(rec("small", ["vad"], state="running", contract="oip-v2", api="infer", usable="segmenter"))
    j1 = jobs.enqueue(session, "label", {}, ["big"])
    j2 = jobs.enqueue(session, "label", {}, ["small"])
    j3 = jobs.enqueue(session, "ingest", {}, [])
    session.commit()
    assert jobs.claim_next(session).id == j2.id  # its model is up: no start, no eviction
    assert jobs.claim_next(session).id == j3.id  # needs no model
    assert jobs.claim_next(session).id == j1.id
    assert jobs.claim_next(session) is None


def test_claim_without_a_catalog_takes_the_oldest(session):
    catalog.set_catalog(down_catalog())
    j1 = jobs.enqueue(session, "label", {}, ["big"])
    jobs.enqueue(session, "label", {}, ["small"])
    session.commit()
    assert jobs.claim_next(session).id == j1.id


def test_recover_requeues_waiting_jobs(session):
    j = jobs.enqueue(session, "label", {}, [])
    j.status = "waiting"
    session.commit()
    assert jobs.recover(session) == 1
    session.commit()
    session.refresh(j)
    assert j.status == "queued"


# ---------------------------------------------------------------- the output index

def _hand_to_bench(session, cat: FakeCatalog, model: str) -> dict[int, dict]:
    """Make Concord Bench the owner of `model`'s outputs: Label's rows go, the index names Bench's copies."""
    rows = session.query(ModelOutput).filter_by(model_name=model).all()
    held = {r.id + 1000: {**cache.served(r), "id": r.id + 1000} for r in rows}
    cat.index_forget("label", [r.id for r in rows])
    cat.register_app("bench", "http://bench.test")
    cat.index_record("bench", [{**index_key(r.build_key, r.audio_sha256, r.span_start, r.span_end, r.params_hash),
                                "output_id": r.id + 1000} for r in rows])
    for r in rows:
        session.delete(r)
    session.commit()
    return held


def test_outputs_bench_holds_are_copied_not_computed(labelled, client, session, replay_catalog, monkeypatch):
    held = _hand_to_bench(session, replay_catalog, SEG)
    reads: list[tuple[str, list[int]]] = []

    def bench_serves(url, ids):
        reads.append((url, list(ids)))
        return {i: held[i] for i in ids if i in held}

    monkeypatch.setattr(cache, "read_outputs", bench_serves)
    pv = preview(client, labelled)
    assert pv["seg"]["state"] == "bench" and pv["seg"]["bench_files"] == 6 and pv["seg"]["cached_files"] == 0
    assert [m["state"] for m in pv["members"]] == ["saved", "saved"]  # cuts known from Bench's segments
    replay_catalog.calls.clear()
    started, res = label(client, labelled, strategy="rover")
    assert started["job"]["required_services"] == [] and leases(replay_catalog) == []
    copies = session.query(ModelOutput).filter_by(model_name=SEG).all()
    assert len(copies) == 6 and all(c.source_app == "bench" and c.source_id in held for c in copies)
    assert {c.build_key for c in copies} == {replay_catalog.model(SEG)["build_key"]}
    assert all(url == "http://bench.test" and len(ids) == 6 for url, ids in reads)  # batched, never per file
    # Label's copies are in the index too, and Label prefers its own entries
    c = copies[0]
    (hit,) = replay_catalog.index_lookup("label", [index_key(c.build_key, c.audio_sha256, c.span_start, c.span_end,
                                                             c.params_hash)])
    assert hit["owner"] == "label" and hit["output_id"] == c.id
    assert res["summary"]["segments"] == labelled["results"]["summary"]["segments"]


def test_a_bench_output_that_is_gone_is_computed_again(labelled, client, session, replay_catalog, monkeypatch):
    _hand_to_bench(session, replay_catalog, SEG)
    monkeypatch.setattr(cache, "read_outputs", lambda url, ids: {})  # Bench no longer has them
    replay_catalog.calls.clear()
    label(client, labelled, strategy="rover")
    assert [a[1] for n, a in leases(replay_catalog) if n == "lease"] == [(SEG,)]
    assert session.query(ModelOutput).filter_by(model_name=SEG, source_app=None).count() == 6


def test_nothing_is_indexed_without_a_build_key(label_flow, client, session, replay_catalog):
    replay_catalog._models[SEG]["build_key"] = None  # the catalog is still hashing its weights
    catalog.clear_cache()
    started, _ = label(client, label_flow)
    rows = session.query(ModelOutput).filter_by(model_name=SEG).all()
    assert len(rows) == 6 and all(r.build_key is None for r in rows)
    assert all(k[0][0] is not None for k in replay_catalog._index)
    log = _ok(client.get(f"/api/jobs/{started['job']['id']}"))["log"]
    assert any("no build key" in line for line in log)
    # such outputs never match: the segmenter runs again next time
    replay_catalog.calls.clear()
    label(client, label_flow, strategy="rover")
    assert (SEG,) in [a[1] for n, a in leases(replay_catalog) if n == "lease"]


def test_a_new_build_finds_no_outputs(labelled, client, replay_catalog):
    replay_catalog._models["whisper-large-v3"]["build_key"] = "feedfacefeedface"
    catalog.clear_cache()
    pv = preview(client, labelled)
    by = {m["service"]: m["state"] for m in pv["members"]}
    assert by == {"whisper-large-v3": "new", "faster-whisper-large-v3": "saved"}
    assert pv["seg"]["state"] == "saved"
    ds = labelled["unlabelled"]
    for f in _ok(client.get(f"/api/label/datasets/{ds}/files")):
        ids = [x["id"] for x in _ok(client.get(f"/api/label/files/{f['id']}"))["segments"]]
        if ids:
            _ok(client.post("/api/label/segments/bulk", json={"ids": ids, "action": "confirm"}))
    names = {m["service"] for m in _ok(client.get(f"/api/label/datasets/{ds}/live"))["asr"]["models"]}
    assert names == {"faster-whisper-large-v3"}  # whisper's outputs are from its old build


# ---------------------------------------------------------------- serving outputs, registering the app

def test_outputs_batch_serves_rows_by_id(labelled, client, session):
    seg_row = session.query(ModelOutput).filter_by(model_name=SEG).first()
    asr_row = session.query(ModelOutput).filter(ModelOutput.span_end > 0).first()
    out = _ok(client.post("/api/outputs/batch", json={"ids": [asr_row.id, 999999, seg_row.id, asr_row.id]}))
    a, s = out["outputs"]
    assert [a["id"], s["id"]] == [asr_row.id, seg_row.id]  # missing ids left out, order and duplicates kept once
    assert set(s) >= {"id", "build_key", "audio_sha256", "span_start", "span_end", "params_hash", "params", "output",
                      "latency_s"}
    assert (s["span_start"], s["span_end"]) == (-1.0, -1.0)  # the whole file, as the index stores it
    assert (a["span_start"], a["span_end"]) == (asr_row.span_start, asr_row.span_end)
    assert s["params"] == {"adapter": "OIPSegmenter"} and s["build_key"] == seg_row.build_key
    assert client.post("/api/outputs/batch", json={"ids": []}).json() == {"outputs": []}


def test_the_app_registers_with_the_catalog_at_startup(data_dir, monkeypatch, fake_catalog):
    from fastapi.testclient import TestClient

    from concord_label.api.app import create_app
    from concord_label.core.settings import reset_settings

    monkeypatch.setenv("PUBLIC_URL", "http://concord-label:8620")
    reset_settings()
    with TestClient(create_app()):
        deadline = time.monotonic() + 5
        while not fake_catalog.apps() and time.monotonic() < deadline:
            time.sleep(0.01)
    assert fake_catalog.apps() == [{"name": "label", "base_url": "http://concord-label:8620"}]


def test_startup_tolerates_a_catalog_that_is_down(data_dir):
    from fastapi.testclient import TestClient

    from concord_label.api.app import create_app

    catalog.set_catalog(down_catalog())
    with TestClient(create_app()) as c:
        assert c.get("/api/config").status_code == 200


def test_replay_catalog_has_the_fixture_models(replay):
    cat = catalog.replay_catalog.__wrapped__(str(replay), "")
    ids = {m["id"] for m in cat.models()}
    assert {SEG, "silero-vad", "nemotron-3-diarization", *ASR_MODELS, "text-normalizer"} <= ids
    assert "qwen3-8b" not in ids  # not a fixture model: not registered
    m = cat.model("faster-whisper-large-v3")
    assert m["capabilities"]["timing_level"] == "word" and m["state"] == "running" and len(m["build_key"]) == 16


@pytest.mark.parametrize("env", ["AICORE_CATALOG", "CATALOG_URL"])
def test_catalog_url_setting(env, monkeypatch, data_dir):
    import os

    from concord_label.core.settings import get_settings, reset_settings

    monkeypatch.setenv(env, "http://catalog:8700/")
    reset_settings()
    s = get_settings()
    assert s.catalog_root == "http://catalog:8700" and s.catalog_ui == "http://catalog:8700"
    assert os.environ["AICORE_CATALOG"] == "http://catalog:8700"  # aicore.client() goes through the gateway
    assert s.public_base_url == "http://127.0.0.1:8620"


def test_job_table_has_no_check_jobs(client):
    assert client.post("/api/registry/silero-vad/check").status_code in (404, 405)
    assert "check" not in jobs.HANDLERS and Job.__table__.c.status is not None


def test_recover_cancels_jobs_cancelled_while_the_worker_was_down(session):
    j = jobs.enqueue(session, "label", {"run_id": -1}, [])
    j.status = "running"
    session.commit()
    paths.job_cancel(j.id).parent.mkdir(parents=True, exist_ok=True)
    paths.job_cancel(j.id).touch()
    assert jobs.recover(session) == 1
    session.commit()
    session.refresh(j)
    assert j.status == "cancelled"
