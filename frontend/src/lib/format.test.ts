import { describe, expect, it } from "vitest";
import { coldStart, duration, etaText, jobStatusText, stageText } from "./format";

describe("catalog wording", () => {
  it("says a queued lease is waiting for compute, with its place in the queue", () => {
    expect(jobStatusText({ status: "waiting", progress: 0, waiting: { position: 2 } })).toBe("waiting for compute #2");
    expect(jobStatusText({ status: "waiting", progress: 0, waiting: { position: null } })).toBe("waiting for compute");
    expect(jobStatusText({ status: "running", progress: 0.42 })).toBe("42%");
    expect(jobStatusText({ status: "blocked", progress: 0 })).toBe("blocked");
    expect(jobStatusText({ status: "running", progress: 0.42, cancelling: true })).toBe("cancelling");
  });

  it("says how long a run has left, and where it is", () => {
    const eta = { remaining_s: 720, active: true, stage: 2, stages: 3, model: "whisper-large-v3", unit: "cuts", done: 41, total: 162, fusing: false };
    expect(etaText({ status: "running", eta })).toBe("about 12 min left");
    expect(etaText({ status: "running", eta: { ...eta, remaining_s: 30 } })).toBe("less than a minute left");
    expect(etaText({ status: "running", eta: { ...eta, remaining_s: null } })).toBe("estimating time left");
    expect(etaText({ status: "waiting", eta })).toBe("about 12 min once it gets compute");
    expect(etaText({ status: "running", eta, cancelling: true })).toBe("");
    expect(etaText({ status: "done", eta })).toBe("");
    expect(etaText({ status: "running", eta: { ...eta, fusing: true } })).toBe("almost done");
    expect(duration(65 * 60)).toBe("65 min");
    expect(duration(100 * 60)).toBe("1 h 40 min");
    expect(stageText(eta)).toBe("Stage 2 of 3: whisper-large-v3, 41 of 162 cuts");
    expect(stageText({ ...eta, total: null })).toBe("Stage 2 of 3: whisper-large-v3");
  });

  it("shows cold starts in seconds or minutes, marking estimates", () => {
    expect(coldStart(21)).toBe("~21 s");
    expect(coldStart(180, false)).toBe("~3 min (est.)");
    expect(coldStart(null)).toBe("—");
  });
});
