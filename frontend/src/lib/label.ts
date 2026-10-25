// Label (Phase 2) helpers shared by the console, pseudo-label, annotate and live pages. The pure
// functions are unit-tested in components/components.test.tsx.

import type { BarKey, SegStatus, Segment } from "./api";
import { C } from "./colors";

export const BAR_ORDER: BarKey[] = ["confirmed", "auto", "review", "rejected", "running", "unlabelled"];

export const BAR_LABELS: Record<BarKey, string> = {
  confirmed: "Confirmed",
  auto: "Auto-accepted",
  review: "Needs review",
  rejected: "Rejected",
  running: "Running",
  unlabelled: "Not labelled",
};

export const BAR_COLORS: Record<BarKey, string> = {
  confirmed: C.good,
  auto: "#0E7C66",
  review: C.warn,
  rejected: C.bad,
  running: C.primary,
  unlabelled: C.line,
};

export const STATUS_LABELS: Record<SegStatus, string> = {
  confirmed: "Confirmed",
  auto: "Auto-accepted",
  pseudo: "Needs review",
  rejected: "Rejected",
};

export const STATUS_BAR: Record<SegStatus, BarKey> = { confirmed: "confirmed", auto: "auto", pseudo: "review", rejected: "rejected" };

export const STATUS_TONE: Record<SegStatus, "good" | "warn" | "bad" | "info"> = { confirmed: "good", auto: "info", pseudo: "warn", rejected: "bad" };

/** A segment's status as shown: a segment drawn by hand and not yet confirmed or rejected is "New · manual". */
export function statusLabel(s: Pick<Segment, "status" | "created">): string {
  return s.created && s.status === "pseudo" ? "New · manual" : STATUS_LABELS[s.status];
}

/** Annotate's keyboard shortcuts: `short` for the strip under the segment table, `does` for the Docs page. */
export const SHORTCUTS: { key: string; short: string; does: string }[] = [
  { key: "Space", short: "play / pause", does: "Plays or pauses from the playhead." },
  { key: "Shift+Space", short: "play selection", does: "Plays the stretch selected on the waveform, or else the selected segment." },
  { key: "← and →", short: "skip 2 s", does: "Moves the playhead 2 seconds back or forward." },
  { key: "↑ and ↓", short: "previous / next", does: "Selects the previous or next segment, and moves the playhead to its start." },
  { key: "Tab and Shift+Tab", short: "next / previous to review", does: "Goes to the next or previous segment to review: one with low confidence, or one with a spelling hint." },
  { key: "Enter", short: "edit text", does: "Starts editing the selected segment's text. In the editor, Enter saves, Esc cancels, and Tab saves and goes to the next segment to review." },
  { key: "Ctrl+Enter", short: "confirm", does: "Confirms the selected segments and goes to the next segment to review. In the editor, it saves the text first." },
  { key: "S", short: "split", does: "Splits the segment under the playhead at the playhead." },
  { key: "M", short: "merge", does: "Merges the selected segments, or the selected segment with the next one." },
  { key: "Delete", short: "delete", does: "Deletes the selected segment." },
  { key: "Ctrl+Z", short: "undo", does: "Undoes the last change to the file's segments." },
  { key: "Ctrl+Shift+Z and Ctrl+Y", short: "redo", does: "Redoes the last change that was undone." },
  { key: "Esc", short: "close / clear", does: "Closes the open menu, then the editor, then clears the selected rows, then the waveform selection." },
  { key: "1 to 8", short: "assign speaker", does: "Gives the selected segments to that speaker." },
  { key: "Alt+Enter", short: "use house spelling", does: "Uses the house spelling for the first spelling hint in the selected segment." },
  { key: "= and -", short: "zoom", does: "Zooms the timeline in or out around the playhead, and Ctrl with the scroll wheel zooms at the pointer." },
  { key: "0", short: "whole file", does: "Shows the whole file on the timeline." },
  { key: "Shift-click", short: "select range", does: "Selects a range of segments in the table." },
];

/** The single keys in a SHORTCUTS entry, e.g. "Ctrl+Shift+Z and Ctrl+Y" -> ["Ctrl+Shift+Z", "Ctrl+Y"]. */
export function shortcutKeys(key: string): string[] {
  if (key === "1 to 8") return ["1", "2", "3", "4", "5", "6", "7", "8"];
  return key.split(" and ");
}

/** Confidence colour (AeroAudio): >= high emerald, >= low amber, below rose, none slate. */
export function confidenceColor(c: number | null | undefined, high = 0.85, low = 0.5): string {
  if (c === null || c === undefined) return C.subtle;
  if (c >= high) return C.good;
  if (c >= low) return C.warn;
  return C.bad;
}

/** Checkbox selection with shift-click ranges over the visible order. */
export function selectRange(order: number[], selected: Set<number>, id: number, anchor: number | null, shift: boolean): Set<number> {
  const next = new Set(selected);
  if (shift && anchor !== null && order.includes(anchor) && order.includes(id)) {
    const a = order.indexOf(anchor);
    const b = order.indexOf(id);
    const [lo, hi] = a < b ? [a, b] : [b, a];
    const on = selected.has(anchor);
    for (const k of order.slice(lo, hi + 1)) {
      if (on) next.add(k);
      else next.delete(k);
    }
    return next;
  }
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Regions where two or more speakers talk at once (rejected segments ignored). */
export function overlapRegions(segs: Pick<Segment, "start" | "end" | "speaker" | "status">[]): { start: number; end: number }[] {
  const ev: [number, number, string][] = [];
  for (const s of segs) {
    if (!s.speaker || s.status === "rejected") continue;
    ev.push([s.start, 1, s.speaker], [s.end, -1, s.speaker]);
  }
  ev.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const active = new Map<string, number>();
  const out: { start: number; end: number }[] = [];
  let open: number | null = null;
  for (const [t, d, spk] of ev) {
    const n0 = (active.get(spk) ?? 0) + d;
    if (n0 > 0) active.set(spk, n0);
    else active.delete(spk);
    const n = active.size;
    if (n >= 2 && open === null) open = t;
    if (n < 2 && open !== null) {
      if (t - open > 1e-3) out.push({ start: open, end: t });
      open = null;
    }
  }
  return out;
}

/** A segment to review: nobody has reviewed it, and its confidence is below `high`. */
export function lowConfidence(s: Pick<Segment, "reviewed" | "status" | "confidence">, high = 0.85): boolean {
  return !s.reviewed && s.status !== "confirmed" && (s.confidence === null || s.confidence < high);
}

/** "Tab" and "Shift+Tab": the next segment to review after `fromId`, or before it when `direction` is -1,
 * wrapping around the file. A segment to review has low confidence, or `also` picks it (Annotate: a segment
 * with a spelling hint). */
export function nextToReview(segs: Segment[], fromId: number | null, direction: 1 | -1 = 1, high = 0.85, also?: (s: Segment) => boolean): number | null {
  const want = (s: Segment) => lowConfidence(s, high) || !!also?.(s);
  const n = segs.length;
  const at = fromId === null ? -1 : segs.findIndex((s) => s.id === fromId);
  const k = at >= 0 ? at : direction === 1 ? -1 : n;
  for (let i = 1; i <= n; i++) {
    const s = segs[(((k + direction * i) % n) + n) % n];
    if (want(s)) return s.id;
  }
  return null;
}

/** The segment playing at time `t` (Annotate highlights it): one that holds `t`, not rejected, the latest
 * start when segments overlap; null between segments. */
export function playingSegment(segs: Pick<Segment, "id" | "start" | "end" | "status">[], t: number): number | null {
  let best: Pick<Segment, "id" | "start"> | null = null;
  for (const s of segs) {
    if (s.status !== "rejected" && s.start <= t && t < s.end && (!best || s.start >= best.start)) best = s;
  }
  return best?.id ?? null;
}

/** The segment at time `t` (a link from Concord Bench): one that contains `t` (not rejected first, then the
 * latest start), else the nearest one. */
export function segmentAt(segs: Segment[], t: number): number | null {
  const inside = segs.filter((s) => s.start <= t && t <= s.end);
  if (inside.length) {
    const ranked = [...inside].sort((a, b) => Number(a.status === "rejected") - Number(b.status === "rejected") || b.start - a.start);
    return ranked[0].id;
  }
  let best: Segment | null = null;
  let gap = Infinity;
  for (const s of segs) {
    const d = Math.min(Math.abs(s.start - t), Math.abs(s.end - t));
    if (d < gap) [best, gap] = [s, d];
  }
  return best?.id ?? null;
}

/** The pseudo-label button: re-fuse saved outputs, or run the members that are not saved first. */
export function labelButton(saved: number, fresh: number): string {
  const total = saved + fresh;
  if (!fresh) return `Re-fuse ${saved} saved output${saved === 1 ? "" : "s"}`;
  return `Run ${fresh} new model${fresh === 1 ? "" : "s"}, then fuse ${total}`;
}

export interface TimedWord {
  /** The word's place in the text. */
  start: number;
  end: number;
  /** Its time in the audio, in seconds. */
  t0: number;
  t1: number;
}

/** The words of `text` with their times, when the word timings still match the text word for word (an edit by
 * hand leaves them stale); null otherwise. */
export function timedWords(text: string | null, words: { w: string; start: number; end: number }[] | null): TimedWord[] | null {
  if (!text || !words?.length) return null;
  const toks = [...text.matchAll(/\S+/g)];
  if (toks.length !== words.length || toks.some((m, k) => m[0] !== words[k].w)) return null;
  return toks.map((m, k) => ({ start: m.index, end: m.index + m[0].length, t0: words[k].start, t1: words[k].end }));
}

/** The word under the playhead `t`, by its index; null between words. */
export function wordAt(words: TimedWord[], t: number): number | null {
  const k = words.findIndex((w) => w.t0 <= t && t < w.t1);
  return k < 0 ? null : k;
}
