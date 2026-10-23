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
  { key: "Space", short: "play / pause", does: "Play or pause" },
  { key: "Tab", short: "next low-confidence", does: "Next low-confidence segment" },
  { key: "1–8", short: "assign speaker", does: "Assign a speaker to the selected segments" },
  { key: "V", short: "split at playhead", does: "Split the segment at the playhead" },
  { key: "M", short: "merge", does: "Merge the selected segments, or the segment with the next one" },
  { key: "Enter", short: "confirm", does: "Confirm the selected segments" },
  { key: "↑ ↓", short: "move", does: "Move between segments" },
  { key: "Shift-click", short: "select range", does: "Select a range of segments" },
];

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

/** The speaker after `current` in the file's speaker list (pill cycling); wraps around. */
export function cycleSpeaker(speakers: string[], current: string | null): string | null {
  if (!speakers.length) return current;
  const k = current === null ? -1 : speakers.indexOf(current);
  return speakers[(k + 1) % speakers.length];
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

/** "Tab": the next unreviewed segment below the auto-accept threshold after `fromId` (wraps). */
export function nextLowConfidence(segs: Segment[], fromId: number | null, high = 0.85): number | null {
  const want = (s: Segment) => !s.reviewed && s.status !== "confirmed" && (s.confidence === null || s.confidence < high);
  const k = fromId === null ? -1 : segs.findIndex((s) => s.id === fromId);
  for (let i = 1; i <= segs.length; i++) {
    const s = segs[(k + i) % segs.length];
    if (s && want(s)) return s.id;
  }
  return null;
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
