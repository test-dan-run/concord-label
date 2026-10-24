import { describe, expect, it } from "vitest";
import { centreOn, clampWindow, fit, follow, isFit, MIN_SPAN, pan, reveal, zoomAt } from "./zoom";

const D = 100;

describe("timeline zoom", () => {
  it("zooms around the anchor, keeping it at the same place on screen", () => {
    const w = zoomAt(fit(D), 4, 50, D);
    expect(w).toEqual({ t0: 37.5, t1: 62.5 });
    const left = zoomAt(fit(D), 4, 20, D);
    expect(left).toEqual({ t0: 15, t1: 40 }); // 20 s stays a fifth of the way across
  });

  it("stops at one second and at the whole file", () => {
    let w = fit(D);
    for (let k = 0; k < 20; k++) w = zoomAt(w, 2, 30, D);
    expect(w.t1 - w.t0).toBe(MIN_SPAN);
    expect(zoomAt({ t0: 10, t1: 60 }, 0.25, 30, D)).toEqual(fit(D));
    expect(zoomAt(fit(0.5), 2, 0.2, 0.5)).toEqual(fit(0.5)); // a file shorter than a second
  });

  it("never leaves the file", () => {
    expect(zoomAt({ t0: 90, t1: 100 }, 0.5, 99, D)).toEqual({ t0: 80, t1: 100 });
    expect(pan({ t0: 10, t1: 20 }, -15, D)).toEqual({ t0: 0, t1: 10 });
    expect(pan({ t0: 80, t1: 90 }, 30, D)).toEqual({ t0: 90, t1: 100 });
    expect(clampWindow({ t0: -5, t1: 200 }, D)).toEqual(fit(D));
    expect(centreOn({ t0: 0, t1: 10 }, 99, D)).toEqual({ t0: 90, t1: 100 });
  });

  it("follows the playhead a page at a time", () => {
    const w = { t0: 0, t1: 10 };
    expect(follow(w, 5, D)).toBe(w);
    expect(follow(w, 10.2, D)).toEqual({ t0: 9.7, t1: 19.7 });
  });

  it("reveals a segment out of sight, centred, or from its start when it is longer than the view", () => {
    const w = { t0: 0, t1: 10 };
    expect(reveal(w, 2, 4, D)).toBe(w);
    expect(reveal(w, 8, 30, D)).toBe(w); // partly in sight: left alone
    expect(reveal(w, 30, 32, D)).toEqual({ t0: 26, t1: 36 });
    expect(reveal(w, 30, 50, D)).toEqual({ t0: 30, t1: 40 });
  });

  it("knows when the whole file shows", () => {
    expect(isFit(fit(D), D)).toBe(true);
    expect(isFit({ t0: 0, t1: 50 }, D)).toBe(false);
  });
});
