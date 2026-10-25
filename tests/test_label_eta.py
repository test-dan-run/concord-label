"""How long a label run has left (label/eta.py): the estimate from saved rates and cold starts, the rate a
stage measures, the job's `eta` as the pages read it, and a replayed run reporting its stages."""

from __future__ import annotations

import time

import pytest

from concord_label.core.models import AudioFile, Dataset, ModelOutput
from concord_label.label.eta import Eta, Stage, counted_down, saved_rtf
from concord_label.runs import jobs
from label_helpers import ASR_MODELS, _ok, label

SEG = "pyannote-diarization"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def eta(*stages: Stage, clock: Clock | None = None, reports: list | None = None) -> Eta:
    return Eta(list(stages), reports.append if reports is not None else None, clock=clock or Clock())


def test_estimate_from_saved_rates_and_cold_starts():
    e = eta(Stage("seg", "seg", 100.0, rtf=0.1, cold_s=20.0), Stage("a", "asr", 100.0, rtf=0.2),
            Stage("b", "asr", 0.0))  # saved: no work, no rate needed
    assert e.remaining() == pytest.approx(10 + 20 + 20)
    j = e.to_json()
    assert (j["stage"], j["stages"], j["model"], j["unit"], j["active"], j["fusing"]) == (1, 3, "seg", "files", False, False)


def test_a_running_stage_uses_the_rate_it_measures():
    clock = Clock()
    e = eta(Stage("seg", "seg", 100.0, rtf=0.1, cold_s=20.0), Stage("a", "asr", 100.0, rtf=0.2), clock=clock)
    e.start("seg", keys=4, audio_s=80.0)  # the lease is active: no cold start, 80 s left to run
    assert e.remaining() == pytest.approx(8 + 20)
    clock.t += 2
    e.advance("seg", 20.0)
    assert e.remaining() == pytest.approx(6 + 20)  # under MIN_MEASURED_S: still the saved rate
    clock.t += 10
    e.advance("seg", 20.0)
    assert e.remaining() == pytest.approx(40 * 0.3 + 20)  # 12 s for 40 s of audio
    j = e.to_json()
    assert (j["done"], j["total"], j["active"]) == (2, 4, True)
    e.finish("seg")
    assert e.remaining() == pytest.approx(20)
    assert e.to_json()["model"] == "a" and e.to_json()["unit"] == "cuts"


def test_an_asr_member_without_saved_outputs_borrows_a_measured_rate():
    clock = Clock()
    e = eta(Stage("seg", "seg", 0.0), Stage("a", "asr", 50.0), Stage("b", "asr", 50.0), clock=clock)
    assert e.remaining() is None  # nothing measured yet
    e.finish("seg")
    e.start("a", keys=10, audio_s=50.0)
    clock.t += 5
    e.advance("a", 25.0)
    assert e.remaining() == pytest.approx(25 * 0.2 + 50 * 0.2)


def test_order_and_cut_audio():
    e = eta(Stage("seg", "seg", 100.0, rtf=0.1), Stage("a", "asr", 100.0, rtf=0.1), Stage("b", "asr", 0.0, rtf=0.1))
    e.order(["b", "a"])
    assert [s.model for s in e.stages] == ["seg", "b", "a"]
    e.asr_audio(60.0)  # the cuts: a saved member keeps no work
    assert [s.audio_s for s in e.stages] == [100.0, 0.0, 60.0]


def test_reports_at_most_once_a_second_unless_forced():
    clock, reports = Clock(), []
    e = eta(Stage("seg", "seg", 10.0, rtf=1.0), clock=clock, reports=reports)
    e.start("seg", keys=3, audio_s=10.0)
    e.advance("seg", 1.0)
    clock.t += 1.5
    e.advance("seg", 1.0)
    e.finish("seg")
    assert len(reports) == 3
    assert reports[-1]["fusing"] and reports[-1]["remaining_s"] == 0 and reports[-1]["model"] is None


def test_counted_down_only_while_inference_runs():
    written = {"remaining_s": 100.0, "at": time.time() - 30, "active": True}
    assert counted_down(written, "running")["remaining_s"] == pytest.approx(70, abs=1)
    assert counted_down(written, "waiting")["remaining_s"] == 100.0
    assert counted_down({**written, "active": False}, "running")["remaining_s"] == 100.0
    assert counted_down({**written, "at": time.time() - 500}, "running")["remaining_s"] == 0
    assert counted_down(None, "running") is None


def test_saved_rtf_from_latency_over_audio(session):
    ds = Dataset(name="d")
    session.add(ds)
    session.flush()
    session.add(AudioFile(dataset_id=ds.id, rel_path="a.wav", sha256="a" * 64, ext="wav", duration=20.0))
    row = dict(model_name="m", params_hash="p", output={}, build_key="b")
    session.add_all([ModelOutput(audio_sha256="a" * 64, latency_s=2.0, **row),  # the whole file: 20 s
                     ModelOutput(audio_sha256="a" * 64, span_start=0.0, span_end=10.0, latency_s=4.0, **row),
                     ModelOutput(audio_sha256="b" * 64, latency_s=9.0, **row)])  # unknown length: skipped
    session.flush()
    assert saved_rtf(session, "m") == pytest.approx(6.0 / 30.0)
    assert saved_rtf(session, "other") is None


def test_a_label_run_reports_its_stages(label_flow, client, replay_catalog, monkeypatch):
    seen = []
    granted = replay_catalog.lease

    def look(holder, models, **kw):
        seen.append(_ok(client.get(f"/api/jobs/{int(holder.rsplit('-', 1)[1])}"))["eta"])
        return granted(holder, models)

    monkeypatch.setattr(replay_catalog, "lease", look)
    started, _ = label(client, label_flow)
    # one lease per stage, each asked for while that stage is current and not yet running inference
    assert [(e["stage"], e["stages"], e["active"]) for e in seen] == [(1, 3, False), (2, 3, False), (3, 3, False)]
    assert seen[0]["model"] == SEG and {e["model"] for e in seen[1:]} == set(ASR_MODELS)
    last = jobs.live_progress(started["job"]["id"])["eta"]
    assert last["fusing"] and last["remaining_s"] == 0
    assert _ok(client.get(f"/api/jobs/{started['job']['id']}"))["eta"] is None  # done: no estimate
