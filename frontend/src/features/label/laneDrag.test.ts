import { describe, expect, it } from "vitest";
import { drawn, laneAt, moved, nextSpeakerName, nudged, timeAt, trimmed } from "./laneDrag";

const seg = { start: 2, end: 4 };

describe("lane drag maths", () => {
  it("maps a pointer to a time inside the file, in milliseconds", () => {
    const rect = { left: 100, width: 400 };
    expect(timeAt(300, rect, 10)).toBe(5);
    expect(timeAt(50, rect, 10)).toBe(0);
    expect(timeAt(900, rect, 10)).toBe(10);
    expect(timeAt(100 + 400 / 3, rect, 10)).toBe(3.333);
  });

  it("moves a segment keeping its length, stopping at either end of the file", () => {
    expect(moved(seg, 5.5, 0.5, 10)).toEqual({ start: 5, end: 7 });
    expect(moved(seg, 0.2, 0.5, 10)).toEqual({ start: 0, end: 2 });
    expect(moved(seg, 9.9, 0.5, 10)).toEqual({ start: 8, end: 10 });
  });

  it("trims either edge, never past the other edge less 0.2 s or outside the file", () => {
    expect(trimmed(seg, "start", 1, 10)).toEqual({ start: 1, end: 4 });
    expect(trimmed(seg, "start", 3.95, 10)).toEqual({ start: 3.8, end: 4 });
    expect(trimmed(seg, "start", -1, 10)).toEqual({ start: 0, end: 4 });
    expect(trimmed(seg, "end", 2.1, 10)).toEqual({ start: 2, end: 2.2 });
    expect(trimmed(seg, "end", 12, 10)).toEqual({ start: 2, end: 10 });
  });

  it("does not stretch a segment that is already shorter than 0.2 s", () => {
    expect(trimmed({ start: 1, end: 1.1 }, "end", 1.05, 10)).toEqual({ start: 1, end: 1.1 });
    expect(trimmed({ start: 1, end: 1.1 }, "start", 0.9, 10)).toEqual({ start: 0.9, end: 1.1 });
  });

  it("nudges an edge by a step", () => {
    expect(nudged(seg, "end", 0.05, 10)).toEqual({ start: 2, end: 4.05 });
    expect(nudged(seg, "start", -0.5, 10)).toEqual({ start: 1.5, end: 4 });
  });

  it("draws a span either way and ignores ones shorter than 0.2 s", () => {
    expect(drawn(5, 3)).toEqual({ start: 3, end: 5, ok: true });
    expect(drawn(3, 3.1).ok).toBe(false);
  });

  it("finds the lane under the pointer", () => {
    const lanes = [{ key: "spk0", top: 0, bottom: 22 }, { key: "spk1", top: 26, bottom: 48 }];
    expect(laneAt(30, lanes)).toBe("spk1");
    expect(laneAt(24, lanes)).toBeUndefined();
  });

  it("names an added speaker with the first free spkN", () => {
    expect(nextSpeakerName(["spk0", "spk1"])).toBe("spk2");
    expect(nextSpeakerName(["spk1", "agent"])).toBe("spk0");
  });
});
