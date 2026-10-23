// Pure maths for editing segments on Annotate's speaker lanes: pointer x -> time, moving, trimming and
// nudging a segment, drawing a new one, and which lane a pointer is over. Times are seconds, rounded to
// milliseconds as the backend stores them.

/** Shortest segment a drag can draw or trim to. A segment already shorter is never stretched by a trim. */
export const MIN_LEN = 0.2;
/** Most speaker lanes a file can have (the 1–8 keys). */
export const MAX_SPEAKERS = 8;
/** Arrow-key nudge of a focused grip, and with Shift. */
export const NUDGE = 0.05;
export const NUDGE_SHIFT = 0.5;

export interface Span {
  start: number;
  end: number;
}

export const ms = (t: number): number => Math.round(t * 1000) / 1000;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** The time under a pointer at `clientX` on a lane spanning `rect`, for a file of `duration` seconds. */
export function timeAt(clientX: number, rect: { left: number; width: number }, duration: number): number {
  return ms(clamp((clientX - rect.left) / Math.max(1, rect.width), 0, 1) * duration);
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
