// Pure maths for editing segments on Annotate's speaker lanes: pointer x -> time, moving, trimming and
// nudging a segment, drawing a new one or placing one with a double-click, edges that segments share, and
// which lane a pointer is over. Times are seconds, rounded to
// milliseconds as the backend stores them.

/** Shortest segment a drag can draw or trim to. A segment already shorter is never stretched by a trim. */
export const MIN_LEN = 0.2;
/** Most speaker lanes a file can have (the 1–8 keys). */
export const MAX_SPEAKERS = 8;
/** Arrow-key nudge of a focused grip, and with Shift. */
export const NUDGE = 0.05;
export const NUDGE_SHIFT = 0.5;
/** Two segments touch when one ends within this many seconds of where the other starts (as the backend). */
export const TOUCH = 0.02;
/** Length of a segment added with a double-click. */
export const PLACED_LEN = 2;

export interface Span {
  start: number;
  end: number;
}

export const ms = (t: number): number => Math.round(t * 1000) / 1000;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** The time under a pointer at `clientX` on a lane spanning `rect` that shows `view` (the whole file, or
 * the zoomed-in part of it). */
export function timeAt(clientX: number, rect: { left: number; width: number }, view: { t0: number; t1: number }): number {
  return ms(view.t0 + clamp((clientX - rect.left) / Math.max(1, rect.width), 0, 1) * (view.t1 - view.t0));
}

/** `seg` dragged so that the point grabbed `offset` seconds after its start sits at `t`; length kept. */
export function moved(seg: Span, t: number, offset: number, duration: number): Span {
  const len = seg.end - seg.start;
  const start = ms(clamp(t - offset, 0, Math.max(0, duration - len)));
  return { start, end: ms(start + len) };
}

/** `seg` with one edge dragged to `t`, kept inside the file and at least MIN_LEN long. */
export function trimmed(seg: Span, edge: "start" | "end", t: number, duration: number): Span {
  const min = Math.min(MIN_LEN, seg.end - seg.start);
  return edge === "start"
    ? { start: ms(clamp(t, 0, seg.end - min)), end: seg.end }
    : { start: seg.start, end: ms(clamp(t, seg.start + min, duration)) };
}

/** `seg` with one edge moved by `delta` seconds (arrow keys on a focused grip). */
export function nudged(seg: Span, edge: "start" | "end", delta: number, duration: number): Span {
  return trimmed(seg, edge, seg[edge] + delta, duration);
}

/** The span drawn between two pointer times, and whether it is long enough to become a segment. */
export function drawn(a: number, b: number): Span & { ok: boolean } {
  const start = Math.min(a, b);
  const end = Math.max(a, b);
  return { start, end, ok: end - start >= MIN_LEN };
}

/** The lane whose row contains `y` (client coordinates); undefined between or outside the lanes. */
export function laneAt<K>(y: number, lanes: { key: K; top: number; bottom: number }[]): K | undefined {
  return lanes.find((l) => y >= l.top && y <= l.bottom)?.key;
}

/** The first `spkN` name not taken, for a speaker added by hand. */
export function nextSpeakerName(taken: string[]): string {
  const have = new Set(taken);
  let n = 0;
  while (have.has(`spk${n}`)) n++;
  return `spk${n}`;
}

/** The segments that share `seg`'s `edge`, on any lane: for its end, the ones that start there; for its start,
 * the ones that end there. Dragging that edge moves them all, so a change of speaker stays in one place. */
export function touching<S extends Span & { id: number }>(segs: S[], seg: S, edge: "start" | "end"): S[] {
  return segs.filter((o) => o.id !== seg.id && Math.abs((edge === "end" ? o.start : o.end) - seg[edge]) <= TOUCH);
}

/** A shared edge dragged to `t`: inside the file, and leaving each segment that ends there (`left`) and each
 * that starts there (`right`) at least MIN_LEN long (or as long as it was, when shorter). */
export function boundaryAt(left: Span[], right: Span[], t: number, duration: number): number {
  const lo = Math.max(0, ...left.map((s) => s.start + Math.min(MIN_LEN, s.end - s.start)));
  const hi = Math.min(duration, ...right.map((s) => s.end - Math.min(MIN_LEN, s.end - s.start)));
  return ms(clamp(t, lo, Math.max(lo, hi)));
}

/** The segment a double-click at `t` adds to a lane: PLACED_LEN seconds centred on `t`, stopping short of the
 * lane's segments on either side and inside the file. Null on a segment, or where there is no room for one. */
export function placed(t: number, lane: Span[], duration: number): Span | null {
  if (lane.some((s) => s.start < t && t < s.end)) return null;
  const prev = Math.max(0, ...lane.filter((s) => s.end <= t).map((s) => s.end));
  const next = Math.min(duration, ...lane.filter((s) => s.start >= t).map((s) => s.start));
  const start = ms(Math.max(prev, t - PLACED_LEN / 2));
  const end = ms(Math.min(next, t + PLACED_LEN / 2));
  return end - start >= MIN_LEN ? { start, end } : null;
}
