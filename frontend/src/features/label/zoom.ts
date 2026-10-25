// Pure maths for zooming Annotate's timeline: the visible window [t0, t1] of a file, zoomed around a
// point, panned, and moved to keep the playhead or a segment in sight. Every result lies inside the file.

export interface Window {
  t0: number;
  t1: number;
}

/** Narrowest window (seconds): the waveform peaks are 10 ms apart, so less shows no more detail. */
export const MIN_SPAN = 1;
/** One zoom step (button or key). */
export const STEP = 2;

const span = (w: Window) => w.t1 - w.t0;

export function fit(duration: number): Window {
  return { t0: 0, t1: Math.max(duration, 1e-3) };
}

/** `w` shifted to lie inside [0, duration], keeping its span (or the whole file when it is longer). */
export function clampWindow(w: Window, duration: number): Window {
  const s = Math.min(span(w), duration);
  const t0 = Math.max(0, Math.min(w.t0, duration - s));
  return { t0, t1: t0 + s };
}

/** `w` zoomed by `factor` (> 1 zooms in) keeping `anchor` at the same place on screen. */
export function zoomAt(w: Window, factor: number, anchor: number, duration: number): Window {
  const s = Math.max(Math.min(MIN_SPAN, duration), Math.min(duration, span(w) / factor));
  const a = Math.max(w.t0, Math.min(w.t1, anchor));
  const frac = span(w) > 0 ? (a - w.t0) / span(w) : 0.5;
  return clampWindow({ t0: a - frac * s, t1: a - frac * s + s }, duration);
}

/** `w` moved by `dt` seconds. */
export function pan(w: Window, dt: number, duration: number): Window {
  return clampWindow({ t0: w.t0 + dt, t1: w.t1 + dt }, duration);
}

/** `w` centred on `t`. */
export function centreOn(w: Window, t: number, duration: number): Window {
  const s = span(w);
  return clampWindow({ t0: t - s / 2, t1: t + s / 2 }, duration);
}

/** While playing: once the playhead leaves `w`, the next page, starting a little before the playhead. */
export function follow(w: Window, t: number, duration: number): Window {
  if (t >= w.t0 && t <= w.t1) return w;
  return clampWindow({ t0: t - span(w) * 0.05, t1: t - span(w) * 0.05 + span(w) }, duration);
}

/** `w` moved so that [start, end] comes into sight (its start, when it is longer than `w`). A segment
 * already partly in sight leaves `w` alone, so grabbing one on the lanes never moves the view under the
 * pointer. */
export function reveal(w: Window, start: number, end: number, duration: number): Window {
  if (end >= w.t0 && start <= w.t1) return w;
  if (end - start >= span(w)) return clampWindow({ t0: start, t1: start + span(w) }, duration);
  return centreOn(w, (start + end) / 2, duration);
}

/** Whether `w` shows the whole file. */
export function isFit(w: Window, duration: number): boolean {
  return w.t0 <= 1e-6 && w.t1 >= duration - 1e-6;
}

/** A wheel turn over the timeline. With `zoom` (Ctrl or ⌘, or a trackpad pinch) it zooms at `frac` of the
 * way across the lanes; otherwise it pans by the larger of the two deltas, where `width` pixels is the whole
 * window. Deltas are in pixels. */
export function wheeled(w: Window, wheel: { dx: number; dy: number; zoom: boolean }, frac: number, width: number, duration: number): Window {
  if (wheel.zoom) return zoomAt(w, Math.exp(-wheel.dy * 0.002), w.t0 + frac * span(w), duration);
  const d = Math.abs(wheel.dx) > Math.abs(wheel.dy) ? wheel.dx : wheel.dy;
  return pan(w, (d / Math.max(1, width)) * span(w), duration);
}
