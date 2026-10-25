// Zoom for Annotate's timeline: buttons (and the =, −, 0 keys in Annotate), Ctrl/⌘ + scroll to zoom
// at the pointer, a plain scroll to pan, and an overview strip of the whole file whose window can be
// dragged. The Timeline and the speaker lanes both show the same window.

import { useEffect, useRef, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import * as I from "../../components/icons";
import { LABEL_W } from "../../components/Timeline";
import { Button } from "../../components/ui";
import { clock } from "../../lib/format";
import { centreOn, fit, isFit, MIN_SPAN, pan, STEP, wheeled, zoomAt, type Window } from "./zoom";

/** Zoom buttons and what the window shows. `anchor` is where a button zooms around (the playhead). */
export function ZoomBar({ view, duration, anchor, onView }: { view: Window; duration: number; anchor: number; onView: (w: Window) => void }) {
  const whole = isFit(view, duration);
  const s = view.t1 - view.t0;
  return (
    <div role="group" aria-label="Timeline zoom" className="flex flex-wrap items-center justify-end gap-2 text-xs text-muted">
      <span className="num" aria-live="polite">
        {whole ? "Whole file" : <>{clock(view.t0)} – {clock(view.t1)} · {s < 10 ? s.toFixed(1) : Math.round(s)} s</>}
      </span>
      <span className="hidden 2xl:inline text-subtle">Scroll to pan · Ctrl + scroll to zoom</span>
      <Button size="sm" aria-label="Zoom out (−)" disabled={whole} onClick={() => onView(zoomAt(view, 1 / STEP, anchor, duration))}>
        <span aria-hidden className="num text-sm leading-none">−</span>
      </Button>
      <Button size="sm" aria-label="Zoom in (=)" disabled={s <= Math.min(MIN_SPAN, duration) + 1e-6} onClick={() => onView(zoomAt(view, STEP, anchor, duration))}>
        <I.Plus size={12} />
      </Button>
      <Button size="sm" disabled={whole} onClick={() => onView(fit(duration))}>Fit file (0)</Button>
    </div>
  );
}

/** The whole file with the visible window marked: drag the window to pan, click elsewhere to centre there. */
export function Overview({ view, duration, time, onView }: { view: Window; duration: number; time: number; onView: (w: Window) => void }) {
  const track = useRef<HTMLDivElement>(null);
  const pct = (t: number) => `${(100 * t) / Math.max(1e-9, duration)}%`;
  const at = (clientX: number) => {
    const r = track.current!.getBoundingClientRect();
    return (Math.max(0, Math.min(r.width, clientX - r.left)) / Math.max(1, r.width)) * duration;
  };
  const down = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !track.current) return;
    e.preventDefault();
    const t = at(e.clientX);
    const onThumb = t >= view.t0 && t <= view.t1;
    const start = onThumb ? view : centreOn(view, t, duration);
    if (!onThumb) onView(start);
    const grabbed = t;
    const move = (ev: PointerEvent) => onView(pan(start, at(ev.clientX) - grabbed, duration));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div className="flex items-center mt-1">
      <div style={{ width: LABEL_W }} className="shrink-0 text-[10px] text-subtle uppercase tracking-wider">Overview</div>
      <div ref={track} onPointerDown={down} className="relative flex-1 h-3 bg-track rounded-sm cursor-pointer touch-none select-none" aria-hidden>
        <span
          className="absolute top-0 bottom-0 rounded-sm border border-focus/70 bg-focus/20 cursor-grab"
          style={{ left: pct(view.t0), width: `max(4px, ${pct(view.t1 - view.t0)})` }}
        />
        {time >= 0 && time <= duration && <span className="absolute top-0 bottom-0 w-px bg-focus pointer-events-none" style={{ left: pct(time) }} />}
      </div>
    </div>
  );
}

/** Ctrl/⌘ + wheel (and a trackpad pinch) zooms at the pointer; any other wheel pans, so the page does not
 * scroll while the pointer is over the timeline. On `box`, whose lanes start LABEL_W from its left. */
export function useWheelZoom(box: RefObject<HTMLDivElement | null>, enabled: boolean, duration: number, setView: (f: (w: Window) => Window) => void) {
  const latest = useRef({ duration, setView });
  latest.current = { duration, setView };
  useEffect(() => {
    const el = box.current;
    if (!enabled || !el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { duration: d, setView: set } = latest.current;
      const r = el.getBoundingClientRect();
      const width = Math.max(1, r.width - LABEL_W);
      const frac = Math.max(0, Math.min(1, (e.clientX - r.left - LABEL_W) / width));
      const px = e.deltaMode === 1 ? 16 : 1; // lines to pixels
      const wheel = { dx: e.deltaX * px, dy: e.deltaY * px, zoom: e.ctrlKey || e.metaKey };
      set((w) => wheeled(w, wheel, frac, width, d));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [box, enabled]);
}
