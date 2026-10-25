// Annotate's transport row, above the timeline: play and pause from the playhead, the clock and the speed
// on the left; the waveform selection and the zoom in the middle; Split, Merge and Delete on the right.
// It renders the page's <audio> element. The speed carries over to the next file through localStorage.

import { useEffect, type ReactNode } from "react";
import type { AudioController } from "../../components/AudioPlayer";
import * as I from "../../components/icons";
import { Button, Select } from "../../components/ui";
import { clock } from "../../lib/format";
import { ZoomBar } from "./TimelineZoom";
import type { Window } from "./zoom";

export const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2];
const RATE_KEY = "concord-label.annotate.rate";

function storedRate(): number {
  try {
    const r = Number(localStorage.getItem(RATE_KEY));
    return RATES.includes(r) ? r : 1;
  } catch {
    return 1;
  }
}

function storeRate(r: number) {
  try {
    localStorage.setItem(RATE_KEY, String(r));
  } catch {
    // private window or blocked storage: the speed just doesn't carry over
  }
}

/** m:ss, e.g. "1:23" (the clock beside the play button). */
export function short(t: number): string {
  const s = Math.max(0, Math.floor(t));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export interface EditButton {
  onClick: () => void;
  disabled: boolean;
  title: string;
}

export function Transport({
  ctl,
  duration,
  range,
  onClearRange,
  view,
  onView,
  split,
  merge,
  remove,
  children,
}: {
  ctl: AudioController;
  duration: number;
  range: Window | null;
  onClearRange: () => void;
  view: Window;
  onView: (w: Window) => void;
  split: EditButton;
  merge: EditButton;
  remove: EditButton;
  /** More buttons at the start of the right-hand group. */
  children?: ReactNode;
}) {
  const { setRate } = ctl;
  useEffect(() => setRate(storedRate()), [setRate]);
  const anchor = ctl.time >= view.t0 && ctl.time <= view.t1 ? ctl.time : (view.t0 + view.t1) / 2;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <audio ref={ctl.audioRef} src={ctl.src} preload="none" />
      <span className="flex items-center gap-3">
        <Button
          variant="primary"
          size="sm"
          className="!min-h-9 !w-9 !px-0 rounded-full"
          aria-label={ctl.playing ? "Pause" : "Play"}
          title={ctl.playing ? "Pause (Space)" : "Play from the playhead (Space)"}
          onClick={() => (ctl.playing ? ctl.pause() : ctl.play(ctl.time >= duration - 0.05 ? 0 : ctl.time))}
        >
          {ctl.playing ? <I.Pause size={14} /> : <I.Play size={14} />}
        </Button>
        <span className="num text-xs text-muted whitespace-nowrap" aria-label="Playhead">
          <span className="text-fg">{short(ctl.time)}</span> / {short(duration)}
        </span>
        <Select
          aria-label="Playback speed"
          value={ctl.rate}
          onChange={(e) => {
            const r = Number(e.target.value);
            setRate(r);
            storeRate(r);
          }}
        >
          {RATES.map((r) => (
            <option key={r} value={r}>
              {r}×
            </option>
          ))}
        </Select>
      </span>
      <span className="flex flex-1 flex-wrap items-center gap-3 min-w-0">
        <RangeBar range={range} ctl={ctl} onClear={onClearRange} />
        <span className="ml-auto">
          <ZoomBar view={view} duration={duration} anchor={anchor} onView={onView} />
        </span>
      </span>
      <span role="group" aria-label="Edit segments" className="flex gap-1.5">
        {children}
        <Button size="sm" title={split.title} disabled={split.disabled} onClick={split.onClick}>Split</Button>
        <Button size="sm" title={merge.title} disabled={merge.disabled} onClick={merge.onClick}>Merge</Button>
        <Button size="sm" title={remove.title} disabled={remove.disabled} onClick={remove.onClick}>Delete</Button>
      </span>
    </div>
  );
}

/** The stretch pulled out on the waveform, to play again or clear; a hint without one. */
function RangeBar({ range, ctl, onClear }: { range: Window | null; ctl: AudioController; onClear: () => void }) {
  if (!range) return <span className="text-xs text-subtle">Drag across the waveform to play a stretch of the file.</span>;
  const inside = ctl.time >= range.t0 && ctl.time < range.t1 - 0.05;
  return (
    <span role="group" aria-label="Waveform selection" className="flex flex-wrap items-center gap-2 text-xs text-muted">
      Selection
      <span className="num text-fg">{clock(range.t0)} – {clock(range.t1)} · {(range.t1 - range.t0).toFixed(2)} s</span>
      <Button size="sm" aria-label={ctl.playing && inside ? "Pause selection" : "Play selection"} onClick={() => (ctl.playing && inside ? ctl.pause() : ctl.play(inside ? ctl.time : range.t0, range.t1))}>
        {ctl.playing && inside ? <I.Pause size={12} /> : <I.Play size={12} />}
      </Button>
      <Button size="sm" variant="ghost" aria-label="Clear selection" onClick={onClear}>
        <I.X size={12} />
      </Button>
    </span>
  );
}
