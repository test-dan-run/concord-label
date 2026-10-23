import { describe, expect, it } from "vitest";
import { coldStart, jobStatusText } from "./format";

describe("catalog wording", () => {
  it("says a queued lease is waiting for compute, with its place in the queue", () => {
    expect(jobStatusText({ status: "waiting", progress: 0, waiting: { position: 2 } })).toBe("waiting for compute #2");
    expect(jobStatusText({ status: "waiting", progress: 0, waiting: { position: null } })).toBe("waiting for compute");
    expect(jobStatusText({ status: "running", progress: 0.42 })).toBe("42%");
    expect(jobStatusText({ status: "blocked", progress: 0 })).toBe("blocked");
  });

  it("shows cold starts in seconds or minutes, marking estimates", () => {
    expect(coldStart(21)).toBe("~21 s");
    expect(coldStart(180, false)).toBe("~3 min (est.)");
    expect(coldStart(null)).toBe("—");
  });
});
