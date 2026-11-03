// Annotate for a dataset with one transcript per clip (`Labelling.clips` in core/models.py): the waveform, the clip's
// one transcript, what each model heard, and a list of every clip. There are no speakers, lanes, or Split and Merge,
// and the page walks the dataset rather than one file: Ctrl+Enter confirms a clip and plays the next one that isn't
// checked. The keys are Annotate's where they can be (CLIP_SHORTCUTS in lib/label.ts lists them for the strip and the
// Docs page), and Ctrl+Space plays and pauses while you type. A clip with no segment gets one, spanning the clip, when
// you first save text for it or reject it. A clip with several segments (a run with a segmenter, or an earlier
// way of labelling the dataset) is never joined on its own: the page says so and offers to merge them. A clip uploaded
// with a label shows that label beside its pseudo-label, with the words that differ marked.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AnnotateView, type Clip, type ClipList, type ClipSegment, type SegStatus, type Segment } from "../../lib/api";
import { C } from "../../lib/colors";
import { clock, num } from "../../lib/format";
import { CLIP_SHORTCUTS, clipOpen, clipShow, clipToReview, confidenceColor, nextClip, STATUS_TONE, statusLabel, wordDiff, type DiffWord } from "../../lib/label";
import { houseSpelled, Matcher, norm, type ActiveLexicon, type Hint } from "../../lib/lexicon";
import { useAudio } from "../../components/AudioPlayer";
import { Timeline } from "../../components/Timeline";
import * as I from "../../components/icons";
import { Bar, Button, Chip, cx, Empty, ErrorNote, PageHeader, Panel, Select, Spinner } from "../../components/ui";
import { Crumbs, isTyping, KeepUploaded, LabellingSwitch, LabelTabs, LexiconLink, ModelOutputs, Shortcuts, useUploaded } from "./common";
import { Editor, HintBar, Transcript } from "./SegmentRow";
import { Overview, useWheelZoom } from "./TimelineZoom";
import { Transport } from "./Transport";
import { fit, follow, isFit, STEP, zoomAt, type Window } from "./zoom";

type Show = "all" | SegStatus | "empty";
const SHOW: { value: Show; label: string }[] = [
  { value: "all", label: "All" },
  { value: "empty", label: "No transcript" },
  { value: "pseudo", label: "Needs review" },
  { value: "auto", label: "Auto-accepted" },
  { value: "rejected", label: "Rejected" },
  { value: "confirmed", label: "Confirmed" },
];
const SKIP = 2;
/** The height of a row in the clip list, which draws only the rows in sight, so a dataset can have thousands. */
const ROW_H = 54;
const AUTOPLAY_KEY = "concord-label.annotate.autoplay";
const NO_CLIPS: Clip[] = [];

function storedAutoplay(): boolean {
  try {
    return localStorage.getItem(AUTOPLAY_KEY) !== "off";
  } catch {
    return true;
  }
}

function storeAutoplay(on: boolean) {
  try {
    localStorage.setItem(AUTOPLAY_KEY, on ? "on" : "off");
  } catch {
    // private window or blocked storage: the choice just doesn't carry over
  }
}

function clipSegment(s: Segment): ClipSegment {
  return { id: s.id, text: s.text, status: s.status, confidence: s.confidence, flagged: s.flagged, reviewed: s.reviewed, created: s.created, spelling_kept: s.spelling_kept };
}

/** How the page got to a clip: whether to play it, and whether to open the editor if it has no text yet. */
interface Arrival {
  play?: boolean;
  edit?: boolean;
}
const JUST_OPENED: Arrival = {};

/** What the clip panel can do to a clip, for the writes and the moves that outlive the panel. */
interface ClipActions {
  /** Change a clip's segment, creating one over the whole clip when text comes for a clip without one. */
  save: (c: Clip, fields: Partial<Segment>) => void;
  decide: (c: Clip, action: "confirm" | "reject") => void;
  merge: (c: Clip, ids: number[]) => void;
  history: (c: Clip, way: "undo" | "redo") => void;
  confirmNext: (c: Clip) => void;
  rejectNext: (c: Clip) => void;
  review: (c: Clip, direction: 1 | -1) => void;
  step: (c: Clip, direction: 1 | -1) => void;
}

export function ClipAnnotate({ datasetId }: { datasetId: number }) {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const listKey = useMemo(() => ["label-clips", datasetId], [datasetId]);
  const q = useQuery({ queryKey: listKey, queryFn: () => api.get<ClipList>(`/label/datasets/${datasetId}/clips`) });
  const clips = q.data?.clips ?? NO_CLIPS;
  const high = q.data?.thresholds.high ?? 0.85;
  const low = q.data?.thresholds.low ?? 0.5;
  const asked = params.get("status") as Show | null;
  const [show, setShow] = useState<Show>(SHOW.some((o) => o.value === asked) ? (asked as Show) : "all");
  const [search, setSearch] = useState("");
  const [autoplay, setAutoplay] = useState(storedAutoplay);
  const [arrival, setArrival] = useState<Arrival & { id?: number }>({});
  const [note, setNote] = useState("");
  const [error, setError] = useState<{ clip: number; message: string } | null>(null);

  const active = useQuery({ queryKey: ["lexicons-active"], queryFn: () => api.get<{ lexicons: ActiveLexicon[] }>("/lexicons/active") });
  const lexicons = useMemo(() => active.data?.lexicons ?? [], [active.data]);
  const matcher = useMemo(() => new Matcher(lexicons), [lexicons]);
  // Hints by segment object: an edit replaces one row's object, so the others aren't searched again.
  const hintCounts = useMemo(() => new WeakMap<ClipSegment, number>(), [matcher]);
  const hintCount = useCallback(
    (c: Clip) => {
      const s = c.segment;
      if (!s) return 0;
      let n = hintCounts.get(s);
      if (n === undefined) {
        n = matcher.hints(s.text, s.spelling_kept).length;
        hintCounts.set(s, n);
      }
      return n;
    },
    [matcher, hintCounts],
  );
  const toReview = useCallback((c: Clip) => clipToReview(c, high) || hintCount(c) > 0, [high, hintCount]);

  const counts = useMemo(() => {
    const out: Record<string, number> = { all: clips.length };
    for (const c of clips) {
      const k = clipShow(c);
      out[k] = (out[k] ?? 0) + 1;
    }
    return out;
  }, [clips]);
  const reviewCount = useMemo(() => clips.filter(toReview).length, [clips, toReview]);
  const hintTotal = useMemo(() => clips.reduce((a, c) => a + hintCount(c), 0), [clips, hintCount]);
  const visible = useMemo(() => {
    const t = search.trim().toLowerCase();
    return clips.filter((c) => (show === "all" || clipShow(c) === show) && (!t || c.stem.toLowerCase().includes(t) || (c.segment?.text ?? "").toLowerCase().includes(t)));
  }, [clips, show, search]);
  const shown = useMemo(() => new Set(visible.map((c) => c.id)), [visible]);

  // The clip on the screen is the one in the URL, else the first shown that still needs a person.
  const fileParam = Number(params.get("file")) || null;
  const fileId = useMemo(() => {
    if (fileParam && clips.some((c) => c.id === fileParam)) return fileParam;
    return (visible.find(clipOpen) ?? visible[0] ?? clips[0])?.id ?? null;
  }, [fileParam, clips, visible]);
  const clip = useMemo(() => clips.find((c) => c.id === fileId) ?? null, [clips, fileId]);
  const go = useCallback(
    (id: number | null, how: Arrival = {}) => {
      if (id === null) return;
      setArrival({ id, play: !!how.play && autoplay, edit: !!how.edit });
      setNote("");
      const p = new URLSearchParams(params);
      p.set("file", String(id));
      p.delete("segment");
      p.delete("t");
      setParams(p, { replace: true });
    },
    [params, setParams, autoplay],
  );
  useEffect(() => {
    // the page's first clip goes in the URL, so it stays on the screen once it is confirmed
    if (fileId !== null && fileId !== fileParam) {
      const p = new URLSearchParams(params);
      p.set("file", String(fileId));
      setParams(p, { replace: true });
    }
  }, [fileId, fileParam]);
  useEffect(() => {
    // the next clip's transcript is there before you get to it
    const n = fileId === null ? null : nextClip(clips, fileId, 1, (c) => shown.has(c.id) && clipOpen(c));
    if (n !== null) void qc.prefetchQuery({ queryKey: ["annotate", n], queryFn: () => api.get<AnnotateView>(`/label/files/${n}`) });
  }, [fileId]);

  const touched = useCallback(
    (fid: number) => {
      void qc.invalidateQueries({ queryKey: ["annotate", fid] });
      void qc.invalidateQueries({ queryKey: ["label-files", datasetId] });
      void qc.invalidateQueries({ queryKey: ["label-console"] });
      void qc.invalidateQueries({ queryKey: ["label-results", datasetId] });
    },
    [qc, datasetId],
  );
  // A saved segment goes straight into the list and the clip's view, so a dataset's whole list isn't read again for
  // every clip you confirm.
  const settle = useCallback(
    (fid: number, s: Segment) => {
      qc.setQueryData<ClipList>(listKey, (v) => v && { ...v, clips: v.clips.map((c) => (c.id === fid ? { ...c, segments: s.status === "rejected" ? 0 : 1, segment: clipSegment(s) } : c)) });
      qc.setQueryData<AnnotateView>(["annotate", fid], (v) => {
        if (!v) return v;
        const known = v.segments.some((x) => x.id === s.id);
        return { ...v, segments: known ? v.segments.map((x) => (x.id === s.id ? { ...x, ...s } : x)) : [...v.segments, s] };
      });
      touched(fid);
    },
    [qc, listKey, touched],
  );
  // Writes run one at a time, in order: a confirm that follows a text edit must not overtake it, and it needs the
  // segment that the edit may have created. A clip's segment is read from the list when its turn comes. When a write
  // fails, the writes waiting behind it on that clip are dropped, so the old text is never confirmed in place of text
  // that didn't save. The page has moved on by then, so the error names the clip, and it stays until that clip saves.
  const tail = useRef<Promise<unknown>>(Promise.resolve());
  const waiting = useRef(0);
  const failed = useRef(new Set<number>());
  const write = useCallback((c: Clip | null, failure: string, fn: () => Promise<unknown>) => {
    const id = c?.id ?? 0;
    waiting.current += 1;
    tail.current = tail.current.then(async () => {
      try {
        if (failed.current.has(id)) return;
        await fn();
        setError((e) => (e?.clip === id ? null : e));
      } catch (e) {
        failed.current.add(id);
        setError({ clip: id, message: `${failure} (${(e as Error).message})` });
      } finally {
        waiting.current -= 1;
        if (!waiting.current) failed.current.clear();
      }
    });
    return tail.current;
  }, []);
  const segmentOf = useCallback((fid: number) => qc.getQueryData<ClipList>(listKey)?.clips.find((c) => c.id === fid)?.segment ?? null, [qc, listKey]);
  const makeSegment = useCallback((c: Clip, text: string | null) => api.post<Segment>(`/label/files/${c.id}/segments`, { start: 0, end: c.duration ?? 0, speaker: null, text }), []);
  const actions: ClipActions = useMemo(() => {
    const save: ClipActions["save"] = (c, fields) =>
      void write(c, fields.text != null ? `${c.stem} wasn't saved, and nothing after it was done. Your text: "${fields.text}"` : `${c.stem} wasn't changed`, async () => {
        const s = segmentOf(c.id);
        settle(c.id, s ? await api.patch<Segment>(`/label/segments/${s.id}`, fields) : await makeSegment(c, fields.text ?? null));
      });
    const decide: ClipActions["decide"] = (c, action) =>
      void write(c, `${c.stem} wasn't ${action === "confirm" ? "confirmed" : "rejected"}`, async () => {
        let s = segmentOf(c.id);
        if (action === "confirm" && s?.text == null) throw new Error("it has no transcript");
        if (!s) {
          // settled at once, so a failure below leaves the list knowing about this segment
          const made = await makeSegment(c, null);
          settle(c.id, made);
          s = clipSegment(made);
        }
        const out = await api.post<{ segments: Segment[] }>(`/label/segments/bulk`, { ids: [s.id], action });
        settle(c.id, out.segments[0]);
      });
    const onward = (c: Clip, what: string) => {
      const n = nextClip(clips, c.id, 1, (o) => shown.has(o.id) && clipOpen(o));
      if (n === null) setNote(`${what}. No other clip is waiting for you here.`);
      go(n, { play: true, edit: true });
    };
    return {
      save,
      decide,
      merge: (c, ids) =>
        void write(c, `${c.stem}'s segments weren't merged`, async () => {
          await api.post(`/label/segments/merge`, { ids });
          await qc.invalidateQueries({ queryKey: listKey });
          touched(c.id);
        }),
      history: (c, way) =>
        void write(c, `${way === "undo" ? "Undo" : "Redo"} on ${c.stem} failed`, async () => {
          const { action } = await api.post<{ action: string }>(`/label/files/${c.id}/${way}`);
          setNote(`${way === "undo" ? "Undid" : "Redid"} ${action}`);
          await qc.invalidateQueries({ queryKey: listKey });
          touched(c.id);
        }),
      confirmNext: (c) => {
        decide(c, "confirm");
        onward(c, "Confirmed");
      },
      rejectNext: (c) => {
        decide(c, "reject");
        onward(c, "Rejected");
      },
      review: (c, direction) => {
        const n = nextClip(clips, c.id, direction, toReview);
        if (n === null) setNote("Nothing left to review");
        go(n, { play: true, edit: true });
      },
      step: (c, direction) => go(nextClip(clips, c.id, direction, (o) => shown.has(o.id))),
    };
  }, [write, segmentOf, makeSegment, settle, clips, shown, toReview, go, qc, listKey, touched]);

  const hasLabels = !!useUploaded(datasetId).data?.labels;
  const confirmAll = useCallback(() => {
    const n = counts.auto ?? 0;
    if (!window.confirm(`Confirm ${n} auto-accepted clip${n === 1 ? "" : "s"} without listening to them?${hasLabels ? " A clip with an uploaded label is auto-accepted only when its pseudo-label matches that label." : ""} They become references, and you can only undo this one clip at a time.`)) return;
    void write(null, "The auto-accepted clips weren't confirmed", async () => {
      await api.post(`/label/datasets/${datasetId}/confirm-auto`);
      await qc.invalidateQueries({ queryKey: listKey });
      if (fileId !== null) touched(fileId);
    });
  }, [counts, hasLabels, write, datasetId, qc, listKey, fileId, touched]);

  const listBox = useRef<HTMLDivElement>(null);
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorNote error={q.error} />;
  if (!clip) {
    return (
      <>
        <PageHeader crumbs={<Crumbs />} title="Annotate" />
        <LabelTabs datasetId={datasetId} current="annotate" />
        <span className="flex"><LabellingSwitch datasetId={datasetId} /></span>
        <Empty>This dataset has no playable clips.</Empty>
      </>
    );
  }
  const index = clips.findIndex((c) => c.id === clip.id);
  const confirmed = counts.confirmed ?? 0;
  return (
    <>
      <PageHeader
        crumbs={<Crumbs />}
        title={clip.stem}
        subtitle={<span className="num">{clip.path} · {clock(clip.duration)} · clip {num(index + 1)} of {num(clips.length)}</span>}
        actions={
          <>
            <Button aria-label="Previous clip" title="Previous clip in the list (↑)" onClick={() => actions.step(clip, -1)}>‹ Prev</Button>
            <Button aria-label="Next clip" title="Next clip in the list (↓)" onClick={() => actions.step(clip, 1)}>Next ›</Button>
          </>
        }
      />
      <LabelTabs datasetId={datasetId} current="annotate" />
      <div className="flex flex-wrap items-center gap-3">
        <LabellingSwitch datasetId={datasetId} />
        <span className="flex items-center gap-2 text-xs text-muted min-w-40">
          <span className="num whitespace-nowrap">{num(confirmed)} of {num(clips.length)} confirmed</span>
          <Bar value={clips.length ? confirmed / clips.length : 0} color={C.good} className="w-20" />
        </span>
        <span role="group" aria-label="Review" className="flex items-center gap-1.5 text-xs text-muted">
          {reviewCount ? (
            <>
              <Button size="sm" variant="ghost" aria-label="Previous to review" title="Previous clip to review (Shift+Tab)" onClick={() => actions.review(clip, -1)}>‹</Button>
              <span className="num">{num(reviewCount)} to review</span>
              <Button size="sm" variant="ghost" aria-label="Next to review" title="Next clip to review (Tab)" onClick={() => actions.review(clip, 1)}>›</Button>
            </>
          ) : (
            "Nothing left to review"
          )}
        </span>
        <span className="ml-auto flex flex-wrap items-center gap-3">
          <span className="text-xs text-muted" aria-live="polite">{note}</span>
          <label className="flex items-center gap-1.5 text-xs text-muted" title="Play each clip when Ctrl+Enter, R or Tab brings you to it">
            <input
              type="checkbox"
              checked={autoplay}
              onChange={(e) => {
                setAutoplay(e.target.checked);
                storeAutoplay(e.target.checked);
              }}
            />
            Play the next clip
          </label>
          <Button size="sm" disabled={!counts.auto} onClick={confirmAll}>
            <I.Check size={13} /> {hasLabels ? "Confirm all auto-accepted" : "Confirm all high-confidence"} ({num(counts.auto ?? 0)})
          </Button>
          <KeepUploaded datasetId={datasetId} />
        </span>
      </div>
      <ErrorNote error={error?.message} />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px] items-start">
        <ClipPanel key={clip.id} datasetId={datasetId} clip={clip} high={high} low={low} matcher={matcher} actions={actions} arrival={arrival.id === clip.id ? arrival : JUST_OPENED} initialTime={Number(params.get("t")) || null} listBox={listBox} />
        <Panel
          title={<span>Clips · {num(visible.length)}</span>}
          subtitle={lexicons.length > 0 ? <LexiconLink lexicons={lexicons} hints={hintTotal} /> : undefined}
        >
          <div className="flex flex-col gap-2">
            <Select label="Show" value={show} onChange={(e) => setShow(e.target.value as Show)}>
              {SHOW.map((o) => (
                <option key={o.value} value={o.value}>{o.label} ({num(counts[o.value] ?? 0)})</option>
              ))}
            </Select>
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search clips by name or transcript"
              placeholder="Search name or transcript"
              className="min-h-8 bg-canvas border border-line rounded-md px-2 text-xs text-fg"
            />
          </div>
          <ClipRows clips={visible} current={clip.id} high={high} low={low} box={listBox} onPick={(id) => go(id)} />
        </Panel>
      </div>
      <Shortcuts list={CLIP_SHORTCUTS} />
    </>
  );
}

/** The clip list. Only the rows in sight (and a few around them) are drawn, and the current clip stays in sight. */
function ClipRows({ clips, current, high, low, box, onPick }: { clips: Clip[]; current: number; high: number; low: number; box: RefObject<HTMLDivElement | null>; onPick: (id: number) => void }) {
  const [top, setTop] = useState(0);
  const [height, setHeight] = useState(600);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [box]);
  useEffect(() => {
    const el = box.current;
    const k = clips.findIndex((c) => c.id === current);
    if (!el || k < 0) return;
    if (k * ROW_H < el.scrollTop) el.scrollTop = k * ROW_H;
    else if ((k + 1) * ROW_H > el.scrollTop + el.clientHeight) el.scrollTop = (k + 1) * ROW_H - el.clientHeight;
  }, [current, clips, box]);
  const first = Math.max(0, Math.floor(top / ROW_H) - 5);
  const last = Math.min(clips.length, Math.ceil((top + height) / ROW_H) + 5);
  return (
    <div ref={box} onScroll={(e) => setTop(e.currentTarget.scrollTop)} className="overflow-auto scroll-thin max-h-[65vh] border border-track rounded-md">
      {clips.length === 0 && <div className="p-3 text-[13px] text-muted">No clips match.</div>}
      <ul aria-label="Clips" className="m-0 p-0 list-none relative" style={{ height: clips.length * ROW_H }}>
        {clips.slice(first, last).map((c, i) => {
          const s = c.segment;
          return (
            <li key={c.id} className="absolute inset-x-0" style={{ top: (first + i) * ROW_H, height: ROW_H }} aria-setsize={clips.length} aria-posinset={first + i + 1}>
              <button
                type="button"
                aria-current={c.id === current || undefined}
                onClick={(e) => {
                  onPick(c.id);
                  e.currentTarget.blur(); // so Enter and Space go to the page rather than to this row
                }}
                className={cx("h-full w-full flex flex-col justify-center gap-0.5 px-2.5 text-left border-0 border-b border-track cursor-pointer", c.id === current ? "bg-active" : "bg-transparent hover:bg-track/60")}
              >
                <span className="flex items-center gap-2 min-w-0">
                  <span className="num text-xs text-fg truncate flex-1 min-w-0">{c.stem}</span>
                  {s?.flagged && <span className="text-warn" title="Flagged"><I.Flag size={11} /></span>}
                  {s && s.confidence !== null && <span className="num text-[11px] font-semibold" style={{ color: confidenceColor(s.confidence, high, low) }}>{s.confidence.toFixed(2)}</span>}
                  {c.segments > 1 ? <Chip tone="warn">{c.segments} segments</Chip> : s ? <Chip tone={STATUS_TONE[s.status]}>{statusLabel(s)}</Chip> : <Chip>No transcript</Chip>}
                </span>
                <span className="text-xs text-muted truncate">{s?.text || <span className="text-subtle">(no transcript)</span>}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** One clip: the waveform with its player, the transcript with its editor, and each model's text. */
function ClipPanel({
  datasetId,
  clip,
  high,
  low,
  matcher,
  actions,
  arrival,
  initialTime,
  listBox,
}: {
  datasetId: number;
  clip: Clip;
  high: number;
  low: number;
  matcher: Matcher;
  actions: ClipActions;
  arrival: Arrival;
  initialTime: number | null;
  listBox: RefObject<HTMLDivElement | null>;
}) {
  const v = useQuery({ queryKey: ["annotate", clip.id], queryFn: () => api.get<AnnotateView>(`/label/files/${clip.id}`) }).data;
  const ctl = useAudio(clip.id);
  const duration = clip.duration || 1;
  const segs = useMemo(() => v?.segments ?? [], [v]);
  const live = useMemo(() => segs.filter((s) => s.status !== "rejected"), [segs]);
  const several = live.length > 1;
  const s = several ? null : live[0] ?? segs[0] ?? null;
  const text = s?.text ?? null;

  const [zoomed, setZoomed] = useState<Window | null>(null);
  const view = zoomed ?? fit(duration);
  const setView = useCallback((f: (w: Window) => Window) => setZoomed((w) => f(w ?? fit(duration))), [duration]);
  const timelineBox = useRef<HTMLDivElement>(null);
  const panelBox = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<Window | null>(null);
  useWheelZoom(timelineBox, true, duration, setView);
  useEffect(() => {
    if (ctl.playing) setView((w) => follow(w, ctl.time, duration));
  }, [ctl.playing, ctl.time, duration, setView]);
  useEffect(() => {
    if (initialTime !== null) ctl.seek(Math.max(0, Math.min(initialTime, duration)));
    else if (arrival.play) ctl.play(0);
  }, []);

  // A clip the review brought you to with nothing to read opens with the editor ready, once its view has loaded.
  const [editing, setEditing] = useState(false);
  const [caret, setCaret] = useState<number | null>(null);
  const opened = useRef(false);
  useEffect(() => {
    if (!v || opened.current) return;
    opened.current = true;
    if (arrival.edit && !several && text === null && !(s && s.status === "rejected")) setEditing(true);
  }, [v]);
  const [hintOpen, setHintOpen] = useState<number | null>(null);
  const hints = useMemo(() => (s ? matcher.hints(s.text, s.spelling_kept) : []), [s, matcher]);
  const hint = hintOpen !== null ? hints[hintOpen] : undefined;
  const applyHint = useCallback((h: Hint) => s?.text != null && actions.save(clip, { text: houseSpelled(s.text, h) }), [s, clip, actions]);
  const keepHint = useCallback((h: Hint) => s && actions.save(clip, { spelling_kept: [...s.spelling_kept, norm(h.found)] }), [s, clip, actions]);

  // The text you just saved, which the segment may not hold yet: Ctrl+Enter in the editor saves and confirms at once.
  const typed = useRef<string | null>(null);
  useEffect(() => {
    typed.current = null; // the segment caught up, or an undo took the text away
  }, [text]);
  const [needsText, setNeedsText] = useState(false);
  const onText = useCallback(
    (t: string) => {
      typed.current = t;
      setNeedsText(false);
      actions.save(clip, { text: t });
    },
    [clip, actions],
  );
  const confirm = useCallback(() => {
    if (several || !v) return;
    if ((typed.current ?? text) === null) {
      setNeedsText(true);
      return;
    }
    actions.confirmNext(clip);
  }, [several, v, text, clip, actions]);

  const [playAt, setPlayAt] = useState(0); // the clip's own Play button starts over; Space goes on from the playhead
  useEffect(() => {
    if (playAt) ctl.play(0);
  }, [playAt]);

  useEffect(() => {
    const tabHere = (t: EventTarget | null) => t === document.body || (t instanceof Node && (!!timelineBox.current?.contains(t) || !!panelBox.current?.contains(t) || !!listBox.current?.contains(t)));
    const toggle = () => {
      if (ctl.playing) ctl.pause();
      else ctl.play(ctl.time >= duration - 0.05 ? 0 : ctl.time);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.metaKey && !e.altKey && e.key === " ") {
        e.preventDefault();
        toggle();
        return;
      }
      if (e.altKey && e.key === "Enter" && !isTyping(e)) {
        if (hints[0]) {
          e.preventDefault();
          applyHint(hints[0]);
        }
        return;
      }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !isTyping(e)) {
        const k = e.key.toLowerCase();
        if (k === "z" || k === "y") {
          e.preventDefault();
          const way = k === "y" || e.shiftKey ? "redo" : "undo";
          if (v?.history[way]) actions.history(clip, way);
        } else if (e.key === "Enter") {
          e.preventDefault();
          confirm();
        }
        return;
      }
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === " ") {
        e.preventDefault();
        if (e.shiftKey) ctl.play(range?.t0 ?? 0, range?.t1);
        else toggle();
      } else if (e.key === "Tab") {
        if (!tabHere(e.target)) return;
        e.preventDefault();
        actions.review(clip, e.shiftKey ? -1 : 1);
      } else if (e.key === "Enter") {
        if ((e.target as HTMLElement | null)?.closest?.("button, a") || several || !v) return;
        e.preventDefault();
        setEditing(true);
      } else if (e.key === "r" || e.key === "R") {
        if (v && !several) actions.rejectNext(clip);
      } else if (e.key === "u" || e.key === "U") {
        if (!several && s?.uploaded_text != null && s.uploaded_text !== s.text) onText(s.uploaded_text);
      } else if (e.key === "=" || e.key === "+") {
        setView((w) => zoomAt(w, STEP, ctl.time >= w.t0 && ctl.time <= w.t1 ? ctl.time : (w.t0 + w.t1) / 2, duration));
      } else if (e.key === "-" || e.key === "_") {
        setView((w) => zoomAt(w, 1 / STEP, ctl.time >= w.t0 && ctl.time <= w.t1 ? ctl.time : (w.t0 + w.t1) / 2, duration));
      } else if (e.key === "0") {
        setView(() => fit(duration));
      } else if (e.key === "Escape") {
        if (hintOpen !== null) setHintOpen(null);
        else setRange(null);
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        ctl.skip(e.key === "ArrowLeft" ? -SKIP : SKIP);
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        actions.step(clip, e.key === "ArrowDown" ? 1 : -1);
      }
    };
    // Space plays and pauses, so its keyup must not also press the button or tick the box that has the focus.
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === " " && !isTyping(e)) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [ctl, clip, v, s, several, hints, hintOpen, range, duration, confirm, applyHint, onText, actions, setView, listBox]);

  const wholeClip = !s || (s.start <= 0.01 && s.end >= duration - 0.01);
  return (
    <div className="flex flex-col gap-5 min-w-0">
      <Panel>
        <Transport ctl={ctl} duration={duration} range={range} onClearRange={() => setRange(null)} view={view} onView={(w) => setView(() => w)} />
        <div ref={timelineBox}>
          <Timeline
            fileId={clip.id}
            t0={view.t0}
            t1={view.t1}
            time={ctl.time}
            markers={s && !wholeClip ? [{ t0: s.start, t1: s.end, color: C.focus }] : []}
            selection={range}
            onSelection={(r) => {
              setRange(r);
              if (r) ctl.play(r.t0, r.t1);
            }}
            onSeek={(t) => ctl.seek(t)}
          />
          {!isFit(view, duration) && <Overview view={view} duration={duration} time={ctl.time} onView={(w) => setView(() => w)} />}
        </div>
      </Panel>
      <div ref={panelBox} className="flex flex-col gap-5">
        <Panel
          title="Transcript"
          subtitle={s && !wholeClip ? <span className="num">Covers {clock(s.start)} to {clock(s.end)} of the clip</span> : undefined}
          actions={
            <span role="group" aria-label="History" className="flex gap-1.5">
              <Button size="sm" disabled={!v?.history.undo} title={v?.history.undo ? `Undo ${v.history.undo} on this clip (Ctrl+Z)` : "Nothing to undo on this clip"} onClick={() => actions.history(clip, "undo")}>Undo</Button>
              <Button size="sm" disabled={!v?.history.redo} title={v?.history.redo ? `Redo ${v.history.redo} on this clip (Ctrl+Shift+Z)` : "Nothing to redo on this clip"} onClick={() => actions.history(clip, "redo")}>Redo</Button>
            </span>
          }
        >
          {!v ? (
            <Spinner />
          ) : several ? (
            <div role="group" aria-label="Several segments" className="flex flex-col gap-2">
              <span className="text-[13px]">
                This clip has <span className="num">{live.length}</span> segments, so it doesn't have one transcript yet. Merge them to review it here, or open it with its speakers.
              </span>
              <ul className="m-0 pl-4 text-[13px] text-muted">
                {live.map((x) => (
                  <li key={x.id}><span className="num">{clock(x.start)}</span> {x.text || <span className="text-subtle">(no transcript)</span>}</li>
                ))}
              </ul>
              <span className="flex flex-wrap gap-2">
                <Button size="sm" variant="primary" onClick={() => actions.merge(clip, live.map((x) => x.id))}>Merge into one</Button>
                <Link to={`/label/annotate/${datasetId}?view=speakers&file=${clip.id}`} className="inline-flex items-center text-xs">Open with speakers</Link>
              </span>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {editing ? (
                <Editor
                  s={{ text, start: 0 }}
                  caret={caret}
                  large
                  placeholder={text === null ? "Type what you hear" : undefined}
                  onText={onText}
                  onEdit={(on) => {
                    setEditing(on);
                    if (!on) setCaret(null);
                  }}
                  onNext={(direction) => actions.review(clip, direction)}
                  onConfirmNext={confirm}
                />
              ) : s && text !== null ? (
                <Transcript
                  s={s}
                  large
                  hints={hints}
                  open={hintOpen}
                  playing
                  ctl={ctl}
                  onFocus={() => undefined}
                  onEdit={(c) => {
                    setCaret(c ?? null);
                    setEditing(true);
                  }}
                  onHint={(k) => setHintOpen((o) => (o === k ? null : k))}
                />
              ) : (
                <button type="button" onClick={() => setEditing(true)} className="px-1.5 py-1 text-left text-base text-subtle bg-transparent border border-dashed border-line rounded cursor-text">
                  No transcript yet. Press Enter or click here to type what you hear.
                </button>
              )}
              {hint && !editing && <HintBar hint={hint} onUse={() => { applyHint(hint); setHintOpen(null); }} onKeep={() => { keepHint(hint); setHintOpen(null); }} />}
              {needsText && <span className="text-xs text-warn">Type a transcript before you confirm, or reject the clip if it has no usable speech.</span>}
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant="primary" onClick={confirm} title="Confirm and go to the next clip (Ctrl+Enter)"><I.Check size={12} /> Confirm and next</Button>
                <Button size="sm" variant="danger" onClick={() => actions.rejectNext(clip)} title="Reject and go to the next clip (R)"><I.X size={12} /> Reject and next</Button>
                <Button size="sm" onClick={() => setPlayAt((n) => n + 1)} title="Play the clip from its start (Shift+Space)"><I.Play size={11} /> Play</Button>
                {s && (
                  <Button size="sm" variant={s.flagged ? "danger" : "outline"} aria-pressed={s.flagged} onClick={() => actions.save(clip, { flagged: !s.flagged })}>
                    <I.Flag size={12} /> {s.flagged ? "Unflag" : "Flag"}
                  </Button>
                )}
                <span className="ml-auto flex items-center gap-2">
                  {s && <span className="num text-xs font-semibold" style={{ color: confidenceColor(s.confidence, high, low) }}>{s.confidence === null ? "—" : s.confidence.toFixed(2)}</span>}
                  {s ? <Chip tone={STATUS_TONE[s.status]}>{statusLabel(s)}</Chip> : <Chip>No transcript</Chip>}
                </span>
              </div>
            </div>
          )}
        </Panel>
        {v && s && !several && s.uploaded_text !== null && <UploadedLabel datasetId={datasetId} s={s} onUse={onText} />}
        {v && s && (
          <Panel>
            <ModelOutputs s={s} onUse={(t) => onText(t)} />
          </Panel>
        )}
      </div>
    </div>
  );
}

function Words({ words }: { words: DiffWord[] }) {
  if (!words.length) return <span className="text-subtle">(empty)</span>;
  return (
    <>
      {words.map((w, i) => (
        <span key={i}>
          {i > 0 && " "}
          {w.differs ? <mark className="bg-warn/25 text-fg rounded-sm px-0.5">{w.word}</mark> : w.word}
        </span>
      ))}
    </>
  );
}

/** The clip's uploaded label against its transcript, for checking one with the other. */
function UploadedLabel({ datasetId, s, onUse }: { datasetId: number; s: Segment; onUse: (t: string) => void }) {
  const label = s.uploaded_text ?? "";
  const text = s.text ?? "";
  const diff = useMemo(() => wordDiff(label, text), [label, text]);
  const noRun = s.source_run_id === null && label === text;
  return (
    <Panel
      title="Uploaded label"
      subtitle={noRun ? undefined : diff.count ? <span className="num">{num(diff.count)} {diff.count === 1 ? "word differs" : "words differ"}</span> : "Same words as the transcript"}
      actions={<Button size="sm" disabled={label === text} title="Use the uploaded label as the transcript (U)" onClick={() => onUse(label)}>Use this text</Button>}
    >
      {noRun ? (
        <span className="text-[13px] text-muted">
          The transcript is this label, and no pseudo-label has been written beside it. <Link to={`/label/pseudo/${datasetId}`}>Pseudo-label</Link> the dataset to compare them.
        </span>
      ) : (
        <dl aria-label="Uploaded label and transcript" className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px] leading-relaxed">
          <dt className="text-xs text-muted pt-0.5">Uploaded</dt>
          <dd className="m-0 break-words" data-uploaded><Words words={diff.a} /></dd>
          <dt className="text-xs text-muted pt-0.5">Transcript</dt>
          <dd className="m-0 break-words" data-compared><Words words={diff.b} /></dd>
        </dl>
      )}
    </Panel>
  );
}
