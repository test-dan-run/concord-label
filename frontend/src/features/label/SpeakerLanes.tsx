// Annotate's speaker lanes, under the waveform: one lane per speaker, where segments are edited directly.
// Drag a bar to move it (onto another lane to give it to that speaker), drag a grip to trim it (or focus
// the grip and use the arrow keys), drag across empty space to draw a new segment. A change is committed
// once, when the pointer is released; until the commit settles the bar stays where it was dropped.
// Lines up with the Timeline above it (same lane-name column).

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import * as I from "../../components/icons";
import { LABEL_W } from "../../components/Timeline";
import { cx } from "../../components/ui";
import { clock } from "../../lib/format";
import { drawn, laneAt, moved, NUDGE, NUDGE_SHIFT, nudged, timeAt, trimmed, type Span } from "./laneDrag";

export interface Lane {
  key: string;
  /** The speaker a segment on this lane gets; null for the lane of segments without a speaker. */
  speaker: string | null;
  label: string;
  color: string;
  /** Added by hand in this session (not from the segments). */
  added?: boolean;
  /** The 1–8 key that gives a selected segment this speaker. */
  hotkey?: number;
}

export interface LaneSeg extends Span {
  id: number;
  speaker: string | null;
  created: boolean;
}

export type LaneEdit = Span & { speaker: string | null };

type Drag =
  | { kind: "move" | "start" | "end"; id: number; offset: number; rect: DOMRect; rows: { key: string; top: number; bottom: number }[]; x0: number; y0: number; moved: boolean; edit: LaneEdit | null }
  | { kind: "create"; lane: Lane; a: number; rect: DOMRect; x0: number; moved: boolean; span: (Span & { ok: boolean }) | null };

const HINT = "Drag a segment to move it or onto another speaker · drag its edges to trim · drag across empty space to add one";
const STRIPES = "repeating-linear-gradient(45deg, rgba(255,255,255,.18) 0 3px, transparent 3px 7px)";
const NUDGE_COMMIT_MS = 500;
const pct = (t: number, duration: number) => `${(100 * t) / Math.max(1e-9, duration)}%`;
const same = (a: LaneEdit, b: LaneEdit) => a.start === b.start && a.end === b.end && a.speaker === b.speaker;

export function SpeakerLanes(props: {
  duration: number;
  lanes: Lane[];
  segs: LaneSeg[];
  currentId: number | null;
  time: number;
  overlaps: Span[];
  flags: Span[];
  note: string;
  canAdd: boolean;
  onAddSpeaker: () => void;
  onSelect: (id: number) => void;
  /** A click on a lane: seek there, and select `id` when the click was on a segment. */
  onSeek: (t: number, id?: number) => void;
  onCommit: (id: number, edit: LaneEdit) => Promise<unknown>;
  onCreate: (edit: LaneEdit) => void;
}) {
  const { duration, lanes, segs, currentId, time, overlaps, flags, note, canAdd, onAddSpeaker } = props;
  const latest = useRef(props);
  latest.current = props;
  const laneEls = useRef(new Map<string, HTMLDivElement>());
  const drag = useRef<Drag | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const nudgeTimer = useRef<number | null>(null);
  // The bar being dragged or nudged, drawn at its new place until the commit settles.
  const [live, setLive] = useState<{ id: number; edit: LaneEdit } | null>(null);
  const liveRef = useRef(live);
  liveRef.current = live;
  const [draft, setDraft] = useState<{ lane: string; span: Span } | null>(null);
  const [tip, setTip] = useState("");

  useEffect(() => () => {
    stop.current?.();
    if (nudgeTimer.current !== null) window.clearTimeout(nudgeTimer.current);
  }, []);

  const laneOf = (speaker: string | null) => lanes.find((l) => l.speaker === speaker);
  const labelOf = (speaker: string | null) => laneOf(speaker)?.label ?? speaker ?? "no speaker";
  const span = (s: Span) => `${clock(s.start)} – ${clock(s.end)}`;

  const commit = (id: number, edit: LaneEdit) => {
    const seg = latest.current.segs.find((s) => s.id === id);
    if (!seg || same(seg, edit)) {
      setLive(null);
      return;
    }
    setLive({ id, edit });
    void latest.current.onCommit(id, edit).catch(() => undefined).finally(() => setLive((l) => (l && l.id === id && same(l.edit, edit) ? null : l)));
  };

  const begin = (e: ReactPointerEvent, d: Drag) => {
    e.preventDefault();
    e.stopPropagation();
    stop.current?.();
    drag.current = d;
    const move = (ev: PointerEvent) => onMove(ev);
    const up = (ev: PointerEvent) => onUp(ev);
    const cancel = () => end(true);
    const key = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") end(true);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key);
    stop.current = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", key);
      stop.current = null;
    };
  };

  const end = (cancelled: boolean) => {
    stop.current?.();
    const d = drag.current;
    drag.current = null;
    setTip("");
    setDraft(null);
    if (cancelled) setLive(null);
    return d;
  };

  const onMove = (ev: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (!d.moved && Math.abs(ev.clientX - d.x0) < 3 && (d.kind === "create" || Math.abs(ev.clientY - d.y0) < 3)) return;
    d.moved = true;
    const { duration: dur, segs: all } = latest.current;
    const t = timeAt(ev.clientX, d.rect, dur);
    if (d.kind === "create") {
      d.span = drawn(d.a, t);
      setDraft({ lane: d.lane.key, span: d.span });
      setTip(`${span(d.span)} · new ${d.lane.label} segment${d.span.ok ? "" : " (too short)"}`);
      return;
    }
    const seg = all.find((s) => s.id === d.id);
    if (!seg) return;
    if (d.kind === "move") {
      const target = laneAt(ev.clientY, d.rows);
      const speaker = target !== undefined ? latest.current.lanes.find((l) => l.key === target)?.speaker ?? seg.speaker : seg.speaker;
      d.edit = { ...moved(seg, t, d.offset, dur), speaker };
      setTip(`${span(d.edit)} · ${labelOf(speaker)}`);
    } else {
      d.edit = { ...trimmed(seg, d.kind, t, dur), speaker: seg.speaker };
      setTip(`${span(d.edit)} · ${(d.edit.end - d.edit.start).toFixed(2)} s`);
    }
    setLive({ id: d.id, edit: d.edit });
  };

  const onUp = (ev: PointerEvent) => {
    const d = end(false);
    if (!d) return;
    if (!d.moved) {
      const t = timeAt(ev.clientX, d.rect, latest.current.duration);
      setLive(null);
      if (d.kind === "create") latest.current.onSeek(t);
      else if (d.kind === "move") latest.current.onSeek(t, d.id);
      return;
    }
    if (d.kind === "create") {
      if (d.span?.ok) latest.current.onCreate({ start: d.span.start, end: d.span.end, speaker: d.lane.speaker });
    } else if (d.edit) {
      commit(d.id, d.edit);
    }
  };

  const rows = () =>
    [...laneEls.current.entries()].map(([key, el]) => {
      const r = el.getBoundingClientRect();
      return { key, top: r.top - 2, bottom: r.bottom + 2 };
    });

  const barDown = (e: ReactPointerEvent, s: LaneSeg, kind: "move" | "start" | "end") => {
    if (e.button !== 0) return;
    const lane = (e.currentTarget as HTMLElement).closest<HTMLElement>("[data-lane]");
    if (!lane) return;
    const rect = lane.getBoundingClientRect();
    const t = timeAt(e.clientX, rect, duration);
    begin(e, { kind, id: s.id, offset: t - s.start, rect, rows: rows(), x0: e.clientX, y0: e.clientY, moved: false, edit: null });
    props.onSelect(s.id);
  };

  const laneDown = (e: ReactPointerEvent<HTMLDivElement>, lane: Lane) => {
    if (e.button !== 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    begin(e, { kind: "create", lane, a: timeAt(e.clientX, rect, duration), rect, x0: e.clientX, moved: false, span: null });
  };

  const gripKey = (e: React.KeyboardEvent, s: LaneSeg, edge: "start" | "end") => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    e.stopPropagation();
    const step = (e.shiftKey ? NUDGE_SHIFT : NUDGE) * (e.key === "ArrowLeft" ? -1 : 1);
    const base = liveRef.current?.id === s.id ? liveRef.current.edit : s;
    const edit = { ...nudged(base, edge, step, duration), speaker: base.speaker };
    setLive({ id: s.id, edit });
    setTip(`${span(edit)} · ${(edit.end - edit.start).toFixed(2)} s`);
    if (nudgeTimer.current !== null) window.clearTimeout(nudgeTimer.current);
    nudgeTimer.current = window.setTimeout(() => flushNudge(s.id), NUDGE_COMMIT_MS);
  };

  const flushNudge = (id: number) => {
    if (nudgeTimer.current !== null) window.clearTimeout(nudgeTimer.current);
    nudgeTimer.current = null;
    const l = liveRef.current;
    if (l && l.id === id && !drag.current) {
      setTip("");
      commit(id, l.edit);
    }
  };

  const shown = segs.map((s) => (live && live.id === s.id ? { ...s, ...live.edit } : s));
  const playX = time >= 0 && time <= duration ? pct(time, duration) : null;
  const playhead = playX && <span className="absolute top-0 bottom-0 w-px bg-focus pointer-events-none z-[2]" style={{ left: playX }} />;

  return (
    <div className="flex flex-col gap-1 mt-1.5 select-none">
      <div className="flex items-center min-h-3">
        <div style={{ width: LABEL_W }} className="shrink-0 text-[10px] text-subtle uppercase tracking-wider">Speakers</div>
        <div className="flex-1 text-right text-[11px] text-subtle hidden md:block">{HINT}</div>
      </div>
      {lanes.map((lane) => {
        const bars = shown.filter((s) => s.speaker === lane.speaker);
        const d = draft && draft.lane === lane.key ? draft.span : null;
        return (
          <div key={lane.key} className="flex items-center">
            <div style={{ width: LABEL_W }} className="shrink-0 flex items-center gap-1.5 text-[11px] text-muted num min-w-0" title={lane.label}>
              <span className="size-2 rounded-full shrink-0" style={{ background: lane.color }} />
              <span className="truncate">{lane.label}</span>
              {lane.added && <span className="font-sans text-subtle whitespace-nowrap">· added</span>}
            </div>
            <div
              data-lane={lane.key}
              ref={(el) => {
                if (el) laneEls.current.set(lane.key, el);
                else laneEls.current.delete(lane.key);
              }}
              onPointerDown={(e) => laneDown(e, lane)}
              className="relative flex-1 h-[22px] bg-track rounded-[3px] cursor-crosshair touch-none"
            >
              {overlaps.map((o, k) => (
                <span key={`o${k}`} className="hatch absolute top-0 bottom-0 pointer-events-none" style={{ left: pct(o.start, duration), width: pct(o.end - o.start, duration) }} />
              ))}
              {bars.map((s) => {
                const sel = s.id === currentId;
                return (
                  <span
                    key={s.id}
                    title={`${lane.label} ${span(s)}${s.created ? " · drawn by hand" : ""}`}
                    onPointerDown={(e) => barDown(e, s, "move")}
                    className={cx("group absolute top-0.5 bottom-0.5 rounded-sm cursor-grab touch-none", sel && "z-[1]")}
                    style={{
                      left: pct(s.start, duration),
                      width: `max(2px, ${pct(s.end - s.start, duration)})`,
                      background: lane.color,
                      backgroundImage: s.created ? STRIPES : undefined,
                      boxShadow: sel ? "0 0 0 2px var(--color-fg)" : undefined,
                    }}
                  >
                    {(["start", "end"] as const).map((edge) => (
                      <button
                        key={edge}
                        type="button"
                        aria-label={`${edge === "start" ? "Start" : "End"} of segment, ${clock(s[edge])}. Drag or use the arrow keys to adjust.`}
                        onPointerDown={(e) => barDown(e, s, edge)}
                        onKeyDown={(e) => gripKey(e, s, edge)}
                        onBlur={() => flushNudge(s.id)}
                        className={cx(
                          "absolute -top-0.5 -bottom-0.5 w-2.5 p-0 border-0 bg-transparent cursor-ew-resize flex items-center justify-center touch-none focus-visible:outline-2 focus-visible:outline-fg",
                          edge === "start" ? "-left-1" : "-right-1",
                          sel ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
                        )}
                      >
                        <span className="w-[3px] h-[70%] rounded-sm bg-fg" />
                      </button>
                    ))}
                  </span>
                );
              })}
              {d && (
                <span
                  className="absolute top-0.5 bottom-0.5 rounded-sm pointer-events-none border border-dashed"
                  style={{ left: pct(d.start, duration), width: pct(d.end - d.start, duration), borderColor: lane.color, background: `${lane.color}40` }}
                />
              )}
              {bars.length === 0 && !d && (
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-subtle pointer-events-none whitespace-nowrap">
                  Drag here to add a segment for {lane.label}
                  {lane.hotkey ? `, or press ${lane.hotkey} on a selected one` : ""}.
                </span>
              )}
              {playhead}
            </div>
          </div>
        );
      })}
      <div className="flex min-h-4">
        <div style={{ width: LABEL_W }} className="shrink-0" />
        <span className="num text-[11px] text-fg">{tip}</span>
      </div>
      <div className="flex items-center flex-wrap gap-y-1">
        <div style={{ width: LABEL_W }} className="shrink-0">
          {canAdd && (
            <button type="button" onClick={onAddSpeaker} className="inline-flex items-center gap-1.5 min-h-[26px] px-2 -ml-2 rounded border border-dashed border-line bg-transparent text-muted hover:text-fg text-[11px] cursor-pointer">
              <I.Plus size={12} /> Add speaker
            </button>
          )}
        </div>
        <span className="text-[11px] text-subtle">{note}</span>
      </div>
      <div className="flex items-center min-h-3 mt-1">
        <div style={{ width: LABEL_W }} className="shrink-0 text-[10px] text-subtle uppercase tracking-wider">Review</div>
      </div>
      <div className="flex items-center">
        <div style={{ width: LABEL_W }} className="shrink-0 flex items-center gap-1.5 text-[11px] text-muted">
          <span className="size-2 rounded-full bg-warn" />
          Flags
        </div>
        <div className="relative flex-1 h-3.5 bg-track rounded-sm">
          {flags.map((f, k) => (
            <span key={k} className="absolute top-0 bottom-0 rounded-sm bg-warn" style={{ left: pct(f.start, duration), width: `max(2px, ${pct(f.end - f.start, duration)})` }} />
          ))}
          {playhead}
        </div>
      </div>
    </div>
  );
}
