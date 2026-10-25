import { describe, expect, it } from "vitest";
import { boundaryAt, drawn, laneAt, MIN_LEN, moved, nextSpeakerName, nudged, placed, timeAt, touching, trimmed } from "./laneDrag";

const seg = { start: 2, end: 4 };

describe("lane drag maths", () => {
  it("maps a pointer to a time inside the visible window, in milliseconds", () => {
    const rect = { left: 100, width: 400 };
    const file = { t0: 0, t1: 10 };
    expect(timeAt(300, rect, file)).toBe(5);
    expect(timeAt(50, rect, file)).toBe(0);
    expect(timeAt(900, rect, file)).toBe(10);
    expect(timeAt(100 + 400 / 3, rect, file)).toBe(3.333);
    expect(timeAt(300, rect, { t0: 20, t1: 22 })).toBe(21); // zoomed in: the lane shows 20–22 s
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

describe("shared edges", () => {
  const segs = [
    { id: 1, start: 0, end: 4 },
    { id: 2, start: 4.01, end: 6 }, // on another lane, 10 ms after 1 ends
    { id: 3, start: 3, end: 5 }, // overlaps, touches nothing
    { id: 4, start: 6, end: 9 },
  ];
  it("finds the segments that start where one ends, and end where one starts, within 20 ms", () => {
    expect(touching(segs, segs[0], "end").map((s) => s.id)).toEqual([2]);
    expect(touching(segs, segs[1], "start").map((s) => s.id)).toEqual([1]);
    expect(touching(segs, segs[1], "end").map((s) => s.id)).toEqual([4]);
    expect(touching(segs, segs[2], "end")).toEqual([]);
    expect(touching(segs, segs[0], "start")).toEqual([]);
  });
  it("keeps a dragged boundary inside both sides", () => {
    expect(boundaryAt([segs[0]], [segs[1]], 5, 10)).toBe(5);
    expect(boundaryAt([segs[0]], [segs[1]], 7, 10)).toBe(6 - MIN_LEN);
    expect(boundaryAt([segs[0]], [segs[1]], -1, 10)).toBe(MIN_LEN);
  });
});

describe("placed (double-click on a lane)", () => {
  const lane = [{ start: 1, end: 2 }, { start: 5, end: 6 }];
  it("adds 2 seconds centred on the click where there is room", () => {
    expect(placed(3.5, lane, 10)).toEqual({ start: 2.5, end: 4.5 });
  });
  it("stops short of the segments next to it, and of the file's ends", () => {
    expect(placed(2.4, lane, 10)).toEqual({ start: 2, end: 3.4 });
    expect(placed(4.8, lane, 10)).toEqual({ start: 3.8, end: 5 });
    expect(placed(0.3, lane, 10)).toEqual({ start: 0, end: 1 });
    expect(placed(9.5, lane, 10)).toEqual({ start: 8.5, end: 10 });
  });
  it("adds nothing on a segment or in a gap too short for one", () => {
    expect(placed(1.5, lane, 10)).toBeNull();
    expect(placed(2.05, [{ start: 1, end: 2 }, { start: 2.1, end: 3 }], 10)).toBeNull();
  });
});
