import { describe, expect, it } from "vitest";
import type { Segment } from "../lib/api";
import { confidenceColor, labelButton, nextToReview, overlapRegions, playingSegment, segmentAt, selectRange, shortcutKeys, SHORTCUTS, timedWords, wordAt } from "../lib/label";
import { C } from "../lib/colors";

function seg(id: number, over: Partial<Segment> = {}): Segment {
  return {
    id, uid: `u${id}`, file_id: 1, start: id, end: id + 1, speaker: "spk0", text: "x", status: "pseudo", confidence: 0.6,
    source_run_id: 1, flagged: false, flag_note: null, reviewed: false, edited_at: null, updated_at: null, chosen: null,
    agreement: null, words: null, created: false, spelling_kept: [], ...over,
  };
}

describe("selectRange", () => {
  const order = [1, 2, 3, 4, 5];
  it("toggles a single row", () => {
    expect([...selectRange(order, new Set(), 3, null, false)]).toEqual([3]);
    expect([...selectRange(order, new Set([3]), 3, 3, false)]).toEqual([]);
  });
  it("shift-click selects the range from the anchor, either direction", () => {
    expect([...selectRange(order, new Set([2]), 4, 2, true)].sort()).toEqual([2, 3, 4]);
    expect([...selectRange(order, new Set([4]), 1, 4, true)].sort()).toEqual([1, 2, 3, 4]);
  });
  it("shift-click from an unselected anchor clears the range", () => {
    expect([...selectRange(order, new Set([1, 2, 3, 4, 5]), 4, 2, true)].length).toBe(5);
    expect([...selectRange(order, new Set([1, 3, 4, 5]), 4, 2, true)].sort()).toEqual([1, 5]);
  });
});

describe("overlapRegions", () => {
  it("finds where two speakers talk at once and ignores rejected segments", () => {
    const segs = [
      seg(1, { start: 0, end: 5, speaker: "a" }),
      seg(2, { start: 4, end: 8, speaker: "b" }),
      seg(3, { start: 6, end: 7, speaker: "a" }),
      seg(4, { start: 1, end: 2, speaker: "c", status: "rejected" }),
    ];
    expect(overlapRegions(segs)).toEqual([{ start: 4, end: 5 }, { start: 6, end: 7 }]);
  });
  it("a speaker overlapping itself is not an overlap", () => {
    expect(overlapRegions([seg(1, { start: 0, end: 3, speaker: "a" }), seg(2, { start: 2, end: 4, speaker: "a" })])).toEqual([]);
  });
});

describe("nextToReview", () => {
  const segs = [seg(1, { confidence: 0.9 }), seg(2, { confidence: 0.4 }), seg(3, { confidence: 0.5, reviewed: true }), seg(4, { confidence: null }), seg(5, { confidence: 0.95 })];
  it("skips high-confidence and reviewed segments, forwards and backwards", () => {
    expect(nextToReview(segs, null)).toBe(2);
    expect(nextToReview(segs, 2)).toBe(4);
    expect(nextToReview(segs, 4, -1)).toBe(2);
    expect(nextToReview(segs, null, -1)).toBe(4);
  });
  it("wraps around at either end", () => {
    expect(nextToReview(segs, 4)).toBe(2);
    expect(nextToReview(segs, 5)).toBe(2);
    expect(nextToReview(segs, 2, -1)).toBe(4);
    expect(nextToReview(segs, 1, -1)).toBe(4);
  });
  it("also takes the segments `also` picks, e.g. ones with a spelling hint", () => {
    expect(nextToReview(segs, 2, 1, 0.85, (s) => s.id === 3)).toBe(3);
    expect(nextToReview(segs, 2, -1, 0.85, (s) => s.id === 5)).toBe(5);
  });
  it("returns null when nothing needs review", () => {
    expect(nextToReview([seg(1, { status: "confirmed", reviewed: true })], null)).toBeNull();
    expect(nextToReview([], 3, -1)).toBeNull();
  });
});

describe("labels and colours", () => {
  it("pseudo-label button reflects saved outputs", () => {
    expect(labelButton(4, 0)).toBe("Re-fuse 4 saved outputs");
    expect(labelButton(4, 1)).toBe("Run 1 new model, then fuse 5");
    expect(labelButton(0, 2)).toBe("Run 2 new models, then fuse 2");
  });
  it("confidence colours follow the thresholds", () => {
    expect(confidenceColor(0.9)).toBe(C.good);
    expect(confidenceColor(0.6)).toBe(C.warn);
    expect(confidenceColor(0.2)).toBe(C.bad);
    expect(confidenceColor(null)).toBe(C.subtle);
  });
});

describe("segmentAt", () => {
  const segs = [seg(1, { start: 0, end: 3 }), seg(2, { start: 2, end: 5, status: "rejected" }), seg(3, { start: 2.5, end: 4 }), seg(4, { start: 8, end: 9 })];
  it("picks a segment containing t, not a rejected one, latest start first", () => {
    expect(segmentAt(segs, 1)).toBe(1);
    expect(segmentAt(segs, 2.7)).toBe(3);
    expect(segmentAt(segs, 4.5)).toBe(2);
  });
  it("falls back to the nearest segment", () => {
    expect(segmentAt(segs, 7)).toBe(4);
    expect(segmentAt([], 1)).toBeNull();
  });
});

describe("playingSegment", () => {
  const segs = [seg(1, { start: 0, end: 2 }), seg(2, { start: 1.5, end: 3 }), seg(3, { start: 4, end: 5, status: "rejected" })];
  it("finds the segment that holds the playhead, the later one where two overlap", () => {
    expect(playingSegment(segs, 0.5)).toBe(1);
    expect(playingSegment(segs, 1.8)).toBe(2);
    expect(playingSegment(segs, 2)).toBe(2); // a segment ends where the next part begins
  });
  it("leaves out rejected segments and gaps", () => {
    expect(playingSegment(segs, 3.5)).toBeNull();
    expect(playingSegment(segs, 4.5)).toBeNull();
  });
});

describe("SHORTCUTS", () => {
  it("lists each key once", () => {
    const keys = SHORTCUTS.flatMap((k) => shortcutKeys(k.key));
    expect(keys.length).toBe(new Set(keys).size);
    expect(keys).toEqual(expect.arrayContaining(["Space", "Shift+Space", "←", "↓", "Tab", "Shift+Tab", "Enter", "Ctrl+Enter", "S", "Delete", "Ctrl+Y", "8"]));
    expect(keys).not.toContain("V");
  });
});

describe("timedWords", () => {
  const words = [{ w: "Hello", start: 1, end: 1.4 }, { w: "there.", start: 1.5, end: 2 }];
  it("places each word in the text with its time", () => {
    const tw = timedWords("Hello  there.", words)!;
    expect(tw).toEqual([{ start: 0, end: 5, t0: 1, t1: 1.4 }, { start: 7, end: 13, t0: 1.5, t1: 2 }]);
    expect(wordAt(tw, 1.6)).toBe(1);
    expect(wordAt(tw, 1.45)).toBeNull();
  });
  it("gives up when the text no longer matches the timings", () => {
    expect(timedWords("Hello there!", words)).toBeNull();
    expect(timedWords("Hello", words)).toBeNull();
    expect(timedWords("Hello there.", null)).toBeNull();
  });
});
