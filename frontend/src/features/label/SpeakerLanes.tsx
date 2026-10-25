// Annotate's speaker lanes, under the waveform: one lane per speaker, where segments are edited directly.
// Drag a bar to move it (onto another lane to give it to that speaker), drag a grip to trim it (or focus
// the grip and use the arrow keys), drag across empty space to draw a new segment, or double-click empty
// space to add a 2 second one. Where segments touch (one ends where another starts, on any lane), a grip
// drag moves the shared edge of all of them; with Alt it moves one edge alone. Double-click a lane's name to
// rename the speaker. A change is committed once, when the pointer is released; until the commit settles
// the bars stay where they were dropped. A bar wide enough shows its text.
// Lines up with the Timeline above it (same lane-name column) and shows the same window of the file
// (`view`; zoomed in, segments outside it are not drawn).

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import * as I from "../../components/icons";
import { LABEL_W } from "../../components/Timeline";
import { cx } from "../../components/ui";
import { clock } from "../../lib/format";
import { boundaryAt, drawn, laneAt, moved, NUDGE, NUDGE_SHIFT, nudged, placed, timeAt, TOUCH, touching, trimmed, type Span } from "./laneDrag";
import type { Window } from "./zoom";

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
  text: string | null;
}

export type LaneEdit = Span & { speaker: string | null };

type Drag =
  | { kind: "move" | "start" | "end"; id: number; offset: number; rect: DOMRect; rows: { key: string; top: number; bottom: number }[]; x0: number; y0: number; moved: boolean; edit: LaneEdit | null }
  | { kind: "create"; lane: Lane; a: number; rect: DOMRect; x0: number; moved: boolean; span: (Span & { ok: boolean }) | null }
  | { kind: "boundary"; left: LaneSeg[]; right: LaneSeg[]; rect: DOMRect; x0: number; y0: number; moved: boolean; t: number | null };

const HINT = "Drag a segment to move it or onto another speaker · drag its edges to trim (Alt: one edge alone) · drag or double-click empty space to add one";
/** A bar at least this wide (px) shows its text. */
const TEXT_PX = 40;
const STRIPES = "repeating-linear-gradient(45deg, rgba(255,255,255,.18) 0 3px, transparent 3px 7px)";
const NUDGE_COMMIT_MS = 500;
const pct = (t: number, view: Window) => `${(100 * (t - view.t0)) / Math.max(1e-9, view.t1 - view.t0)}%`;
const len = (d: number, view: Window) => `${(100 * d) / Math.max(1e-9, view.t1 - view.t0)}%`;
const inView = (s: Span, view: Window) => s.end >= view.t0 && s.start <= view.t1;
const same = (a: LaneEdit, b: LaneEdit) => a.start === b.start && a.end === b.end && a.speaker === b.speaker;

export function SpeakerLanes(props: {
  duration: number;
  /** The part of the file shown, as on the Timeline above. */
  view: Window;
  /** The stretch selected on the waveform, shown across the lanes so you see which segments it cuts. */
  selection?: Window | null;
  lanes: Lane[];
  segs: LaneSeg[];
  currentId: number | null;
  /** The segment under the playhead, outlined unless it is also the selected one. */
  playingId?: number | null;
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
  /** A shared edge dragged to `t`: the segments that end there and the ones that start there. */
  onBoundary: (ends: number[], starts: number[], t: number) => Promise<unknown>;
  onCreate: (edit: LaneEdit) => void;
  /** A lane's speaker renamed (Enter in the field that a double-click on its name opens). */
  onRename: (from: string, to: string) => void;
}) {
  const { duration, view, selection, lanes, segs, currentId, playingId, time, overlaps, flags, note, canAdd, onAddSpeaker } = props;
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
  // The segments of a shared edge being dragged, drawn at their new places until the commit settles.
  const [liveB, setLiveB] = useState<Record<number, Span> | null>(null);
  const [draft, setDraft] = useState<{ lane: string; span: Span } | null>(null);
  const [tip, setTip] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  // The width of a lane in pixels, to tell which bars are wide enough to show their text.
  const box = useRef<HTMLDivElement>(null);
  const [laneW, setLaneW] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setLaneW(Math.max(0, e.contentRect.width - LABEL_W)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

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

  const commitBoundary = (d: Extract<Drag, { kind: "boundary" }>, t: number) => {
    const edits = edgeEdits(d, t);
    setLiveB(edits);
    void latest.current.onBoundary(d.left.map((s) => s.id), d.right.map((s) => s.id), t).catch(() => undefined).finally(() => setLiveB((l) => (l === edits ? null : l)));
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
    if (cancelled) {
      setLive(null);
      setLiveB(null);
    }
    return d;
  };

  const onMove = (ev: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (!d.moved && Math.abs(ev.clientX - d.x0) < 3 && (d.kind === "create" || Math.abs(ev.clientY - d.y0) < 3)) return;
    d.moved = true;
    const { duration: dur, segs: all } = latest.current;
    const t = timeAt(ev.clientX, d.rect, latest.current.view);
    if (d.kind === "boundary") {
      d.t = boundaryAt(d.left, d.right, t, dur);
      setLiveB(edgeEdits(d, d.t));
      setTip(`${clock(d.t)} · edge shared by ${d.left.length + d.right.length} segments`);
      return;
    }
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
      const t = timeAt(ev.clientX, d.rect, latest.current.view);
      setLive(null);
      if (d.kind === "create") latest.current.onSeek(t);
      else if (d.kind === "move") latest.current.onSeek(t, d.id);
      return;
    }
    if (d.kind === "boundary") {
      if (d.t !== null && d.t !== d.left[0].end) commitBoundary(d, d.t);
      else setLiveB(null);
    } else if (d.kind === "create") {
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
    const t = timeAt(e.clientX, rect, view);
    props.onSelect(s.id);
    if (kind !== "move" && !e.altKey && touching(segs, s, kind).length) {
      // every segment that ends or starts at this edge moves with it
      const x = s[kind];
      const left = segs.filter((o) => Math.abs(o.end - x) <= TOUCH);
      const right = segs.filter((o) => Math.abs(o.start - x) <= TOUCH);
      begin(e, { kind: "boundary", left, right, rect, x0: e.clientX, y0: e.clientY, moved: false, t: null });
      return;
    }
    begin(e, { kind, id: s.id, offset: t - s.start, rect, rows: rows(), x0: e.clientX, y0: e.clientY, moved: false, edit: null });
  };

  const laneDouble = (e: ReactMouseEvent<HTMLDivElement>, lane: Lane) => {
    if (e.target !== e.currentTarget) return; // on a bar, not on empty space
    const t = timeAt(e.clientX, e.currentTarget.getBoundingClientRect(), view);
    const span = placed(t, segs.filter((s) => s.speaker === lane.speaker), duration);
    if (span) props.onCreate({ ...span, speaker: lane.speaker });
  };

  const laneDown = (e: ReactPointerEvent<HTMLDivElement>, lane: Lane) => {
    if (e.button !== 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    begin(e, { kind: "create", lane, a: timeAt(e.clientX, rect, view), rect, x0: e.clientX, moved: false, span: null });
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

  const shown = segs.map((s) => (liveB?.[s.id] ? { ...s, ...liveB[s.id] } : live && live.id === s.id ? { ...s, ...live.edit } : s));
  const playX = time >= view.t0 && time <= view.t1 ? pct(time, view) : null;
  const playhead = playX && <span className="absolute top-0 bottom-0 w-px bg-focus pointer-events-none z-[2]" style={{ left: playX }} />;
  const band = selection && inView({ start: selection.t0, end: selection.t1 }, view) && (
    <span className="absolute top-0 bottom-0 bg-fg/10 border-x border-fg/40 pointer-events-none z-[2]" style={{ left: pct(selection.t0, view), width: len(selection.t1 - selection.t0, view) }} />
  );

  return (
    <div ref={box} className="flex flex-col gap-1 mt-1.5 select-none">
      <div className="flex items-center min-h-3">
        <div style={{ width: LABEL_W }} className="shrink-0 text-[10px] text-subtle uppercase tracking-wider">Speakers</div>
        <div className="flex-1 text-right text-[11px] text-subtle hidden md:block">{HINT}</div>
      </div>
      {lanes.map((lane) => {
        const all = shown.filter((s) => s.speaker === lane.speaker);
        const bars = all.filter((s) => inView(s, view));
        const d = draft && draft.lane === lane.key ? draft.span : null;
        return (
          <div key={lane.key} className="flex items-center">
            <div style={{ width: LABEL_W }} className="shrink-0 flex items-center gap-1.5 pr-1.5 text-[11px] text-muted num min-w-0" title={lane.speaker === null ? lane.label : `${lane.label} (double-click to rename)`}>
              <span className="size-2 rounded-full shrink-0" style={{ background: lane.color }} />
              {renaming === lane.key && lane.speaker !== null ? (
                <RenameField
                  name={lane.speaker}
                  onDone={(to) => {
                    setRenaming(null);
                    if (to !== null) props.onRename(lane.speaker!, to);
                  }}
                />
              ) : (
                <span className="truncate" onDoubleClick={() => lane.speaker !== null && setRenaming(lane.key)}>{lane.label}</span>
              )}
              {lane.added && renaming !== lane.key && <span className="font-sans text-subtle whitespace-nowrap">· added</span>}
            </div>
            <div
              data-lane={lane.key}
              ref={(el) => {
                if (el) laneEls.current.set(lane.key, el);
                else laneEls.current.delete(lane.key);
              }}
              onPointerDown={(e) => laneDown(e, lane)}
              onDoubleClick={(e) => laneDouble(e, lane)}
              className="relative flex-1 h-[22px] bg-track rounded-[3px] cursor-crosshair touch-none overflow-clip"
            >
              {overlaps.filter((o) => inView(o, view)).map((o, k) => (
                <span key={`o${k}`} className="hatch absolute top-0 bottom-0 pointer-events-none" style={{ left: pct(o.start, view), width: len(o.end - o.start, view) }} />
              ))}
              {bars.map((s) => {
                const sel = s.id === currentId;
                const playing = !sel && s.id === playingId;
                const wide = s.text && laneW * ((s.end - s.start) / Math.max(1e-9, view.t1 - view.t0)) >= TEXT_PX;
                return (
                  <span
                    key={s.id}
                    title={`${lane.label} ${span(s)}${s.created ? " · drawn by hand" : ""}`}
                    onPointerDown={(e) => barDown(e, s, "move")}
                    data-playing={playing || undefined}
                    className={cx("group absolute top-0.5 bottom-0.5 rounded-sm cursor-grab touch-none", sel && "z-[1]")}
                    style={{
                      left: pct(s.start, view),
                      width: `max(2px, ${len(s.end - s.start, view)})`,
                      background: lane.color,
                      backgroundImage: s.created ? STRIPES : undefined,
                      boxShadow: sel ? "0 0 0 2px var(--color-fg)" : playing ? "0 0 0 2px var(--color-focus)" : undefined,
                    }}
                  >
                    {wide && <span className="absolute inset-0 block px-1.5 truncate text-[10px] leading-[18px] text-canvas pointer-events-none">{s.text}</span>}
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
                  style={{ left: pct(d.start, view), width: len(d.end - d.start, view), borderColor: lane.color, background: `${lane.color}40` }}
                />
              )}
              {all.length === 0 && !d && (
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-subtle pointer-events-none whitespace-nowrap">
                  Drag or double-click here to add a segment for {lane.label}
                  {lane.hotkey ? `, or press ${lane.hotkey} on a selected one` : ""}.
                </span>
              )}
              {band}
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
        <div className="relative flex-1 h-3.5 bg-track rounded-sm overflow-clip">
          {flags.filter((f) => inView(f, view)).map((f, k) => (
            <span key={k} className="absolute top-0 bottom-0 rounded-sm bg-warn" style={{ left: pct(f.start, view), width: `max(2px, ${len(f.end - f.start, view)})` }} />
          ))}
          {band}
          {playhead}
        </div>
      </div>
    </div>
  );
}

/** The new spans of the segments at a shared edge moved to `t`. */
function edgeEdits(d: { left: LaneSeg[]; right: LaneSeg[] }, t: number): Record<number, Span> {
  const out: Record<number, Span> = {};
  for (const s of d.left) out[s.id] = { start: s.start, end: t };
  for (const s of d.right) out[s.id] = { start: t, end: s.end };
  return out;
}

/** The field a double-click on a lane's name opens: Enter renames, Esc or leaving it cancels. */
function RenameField({ name, onDone }: { name: string; onDone: (to: string | null) => void }) {
  const [value, setValue] = useState(name);
  const done = useRef(false);
  const finish = (to: string | null) => {
    if (done.current) return;
    done.current = true;
    onDone(to);
  };
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      finish(value.trim() && value.trim() !== name ? value.trim() : null);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      finish(null);
    }
  };
  return (
    <input
      autoFocus
      aria-label={`Rename ${name}`}
      value={value}
      maxLength={100}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={onKey}
      onBlur={() => finish(null)}
      onFocus={(e) => e.target.select()}
      className="min-w-0 flex-1 bg-canvas border border-focus rounded px-1 h-5 text-[11px] text-fg outline-none"
    />
  );
}
