// Annotate (annotation only, no metrics): whole-file timeline with editable speaker lanes (SpeakerLanes),
// a transport row (Transport), segment grid with multi-select and a bulk bar, inline transcript editing with
// autosave per segment, undo and redo (label/history.py on the server), and the keys of the owner's earlier
// app, pseudolabel (docs/ANNOTATE_PLAN.md; SHORTCUTS in lib/label.ts lists them for the strip and the Docs page):
// Space play/pause from the playhead · Shift+Space play the waveform selection or the segment · ←/→ skip 2 s ·
// ↑/↓ previous/next segment · Tab/Shift+Tab next/previous to review · Enter edit · Ctrl+Enter confirm ·
// S split at the playhead · M merge · Delete · Ctrl+Z undo · Ctrl+Shift+Z or Ctrl+Y redo · Esc close/clear ·
// 1–8 assign speaker · Alt+Enter use house spelling · = - 0 zoom. Variants the published lexicons flag are
// underlined in the transcript with a choice to use the house spelling or keep the text; the text never
// changes on its own.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, qs, type AnnotateView, type LabelFile, type SegStatus, type Segment } from "../../lib/api";
import { houseSpelled, Matcher, norm, type ActiveLexicon, type Hint } from "../../lib/lexicon";
import { C, speakerColors } from "../../lib/colors";
import { clock, num } from "../../lib/format";
import { confidenceColor, cycleSpeaker, nextToReview, overlapRegions, playingSegment, segmentAt, selectRange, SHORTCUTS, STATUS_LABELS, STATUS_TONE, statusLabel } from "../../lib/label";
import { PlayFrom, useAudio } from "../../components/AudioPlayer";
import { Timeline } from "../../components/Timeline";
import * as I from "../../components/icons";
import { Button, Chip, cx, Empty, ErrorNote, PageHeader, Panel, Select, Spinner } from "../../components/ui";
import { Crumbs, DatasetChooser, LabelTabs } from "./common";
import { MAX_SPEAKERS, nextSpeakerName } from "./laneDrag";
import { SpeakerLanes, type Lane, type LaneEdit } from "./SpeakerLanes";
import { Overview, useWheelZoom } from "./TimelineZoom";
import { Transport } from "./Transport";
import { fit, follow, reveal, STEP, zoomAt, type Window } from "./zoom";

type Show = "all" | SegStatus;
/** ← and → move the playhead this many seconds. */
const SKIP = 2;
const SHOW: { value: Show; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pseudo", label: "Needs review" },
  { value: "auto", label: "Auto-accepted" },
  { value: "rejected", label: "Rejected" },
  { value: "confirmed", label: "Confirmed" },
];

/** `/label/file/:fileId` (from error analysis): find the file's dataset, then open the annotator on it. */
export function OpenFile() {
  const { fileId } = useParams();
  const [params] = useSearchParams();
  const q = useQuery({ queryKey: ["audio-info", fileId], queryFn: () => api.get<{ dataset_id: number }>(`/audio/${fileId}/info`) });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Spinner />;
  const seg = params.get("segment");
  return <Navigate replace to={`/label/annotate/${q.data.dataset_id}?file=${fileId}${seg ? `&segment=${seg}` : ""}`} />;
}

/** `/label/file/by-sha/:sha256?catalog_dataset_id=&t=` ("Open in Label" in Concord Bench and in the Dataset Catalog UI,
 * CONTRACTS.md §5): find the file by its audio fingerprint within the working dataset of that catalog dataset, then
 * open the annotator on it at `t`. */
export function OpenFileBySha() {
  const { sha256 } = useParams();
  const [params] = useSearchParams();
  const catalogId = params.get("catalog_dataset_id");
  const q = useQuery({
    queryKey: ["file-by-sha", sha256, catalogId],
    queryFn: () => api.get<{ file_id: number; dataset_id: number }>(`/files/by-sha/${sha256}${qs({ catalog_dataset_id: catalogId })}`),
    retry: false,
  });
  if (q.error) {
    return (
      <>
        <PageHeader title="Open in Label" />
        <ErrorNote error={`This audio is not in Concord Label${catalogId ? " in a dataset made from that Dataset Catalog dataset" : ""}: ${(q.error as Error).message}`} />
        {catalogId && (
          <p className="text-[13px] text-muted">
            To label it here, make a dataset from the catalog with <Link to="/datasets/from-catalog">New from Dataset Catalog</Link>.
          </p>
        )}
      </>
    );
  }
  if (!q.data) return <Spinner />;
  const t = params.get("t");
  return <Navigate replace to={`/label/annotate/${q.data.dataset_id}?file=${q.data.file_id}${t ? `&t=${encodeURIComponent(t)}` : ""}`} />;
}

export function AnnotatePage() {
  const { datasetId } = useParams();
  if (!datasetId) {
    return <DatasetChooser title="Annotate" to={(id) => `/label/annotate/${id}`} subtitle="Review and correct segments: transcript, speakers, boundaries." />;
  }
  return <AnnotateDataset datasetId={Number(datasetId)} />;
}

function AnnotateDataset({ datasetId }: { datasetId: number }) {
  const [params, setParams] = useSearchParams();
  const files = useQuery({ queryKey: ["label-files", datasetId], queryFn: () => api.get<LabelFile[]>(`/label/datasets/${datasetId}/files`) });
  const status = params.get("status") as SegStatus | null;
  const fileParam = params.get("file");
  const fileId = useMemo(() => {
    if (fileParam) return Number(fileParam);
    const list = (files.data ?? []).filter((f) => f.usable);
    const key = status === "pseudo" ? "review" : status;
    const hit = key ? list.find((f) => f.counts[key as keyof LabelFile["counts"]] > 0) : undefined;
    return (hit ?? list[0])?.id ?? null;
  }, [fileParam, files.data, status]);
  const go = (id: number) => {
    const p = new URLSearchParams(params);
    p.set("file", String(id));
    p.delete("segment");
    setParams(p);
  };
  if (files.isLoading) return <Spinner />;
  if (files.error) return <ErrorNote error={files.error} />;
  if (fileId === null) {
    return (
      <>
        <PageHeader crumbs={<Crumbs />} title="Annotate" />
        <LabelTabs datasetId={datasetId} current="annotate" />
        <Empty>This dataset has no playable files.</Empty>
      </>
    );
  }
  const t = params.get("t");
  const initialTime = t !== null && t !== "" && Number.isFinite(Number(t)) ? Number(t) : null;
  return <Annotator key={fileId} datasetId={datasetId} fileId={fileId} files={files.data ?? []} onFile={go} initialSegment={Number(params.get("segment")) || null} initialTime={initialTime} initialShow={status ?? "all"} />;
}

function useLabelMutations(fileId: number, datasetId: number) {
  const qc = useQueryClient();
  const done = () => {
    void qc.invalidateQueries({ queryKey: ["annotate", fileId] });
    void qc.invalidateQueries({ queryKey: ["label-files", datasetId] });
    void qc.invalidateQueries({ queryKey: ["label-console"] });
    void qc.invalidateQueries({ queryKey: ["label-results", datasetId] });
  };
  const patch = useMutation({ mutationFn: ({ id, ...body }: { id: number } & Partial<Segment>) => api.patch<Segment>(`/label/segments/${id}`, body), onSuccess: done });
  const bulk = useMutation({ mutationFn: (b: { ids: number[]; action: string; speaker?: string | null }) => api.post(`/label/segments/bulk`, b), onSuccess: done });
  const merge = useMutation({ mutationFn: (ids: number[]) => api.post<Segment>(`/label/segments/merge`, { ids }), onSuccess: done });
  const split = useMutation({ mutationFn: ({ id, t }: { id: number; t: number }) => api.post<Segment[]>(`/label/segments/${id}/split`, { t }), onSuccess: done });
  // A drag on the speaker lanes: the new span and speaker show at once, and a failed save puts them back.
  const move = useMutation({
    mutationFn: ({ id, ...body }: { id: number } & LaneEdit) => api.patch<Segment>(`/label/segments/${id}`, body),
    onMutate: async ({ id, start, end, speaker }) => {
      await qc.cancelQueries({ queryKey: ["annotate", fileId] });
      qc.setQueryData<AnnotateView>(["annotate", fileId], (v) =>
        v && { ...v, segments: v.segments.map((s) => (s.id === id ? { ...s, start, end, speaker } : s)).sort((a, b) => a.start - b.start || a.end - b.end) },
      );
    },
    onSettled: done,
  });
  const create = useMutation({ mutationFn: (b: { start: number; end: number; speaker: string | null }) => api.post<Segment>(`/label/files/${fileId}/segments`, b), onSuccess: done });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/label/segments/${id}`), onSuccess: done });
  const confirmAuto = useMutation({ mutationFn: () => api.post<{ confirmed: number }>(`/label/files/${fileId}/confirm-auto`), onSuccess: done });
  // Undo and redo (label/history.py): a 409 means the history no longer matches the file, and the server forgot it.
  const history = useMutation({ mutationFn: (way: "undo" | "redo") => api.post<{ action: string; ids: number[] }>(`/label/files/${fileId}/${way}`), onSettled: done });
  const error = patch.error ?? move.error ?? bulk.error ?? merge.error ?? split.error ?? create.error ?? remove.error ?? confirmAuto.error ?? history.error;
  return { patch, move, bulk, merge, split, create, remove, confirmAuto, history, error };
}

const NOT_TEXT = new Set(["checkbox", "radio", "button", "submit", "reset", "range", "file", "color"]);

/** Whether a key goes to a field that takes text (a checkbox or a button does not: the shortcuts apply). */
function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  if (t.tagName === "INPUT") return !NOT_TEXT.has((t as HTMLInputElement).type);
  return t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable;
}

function Annotator({
  datasetId,
  fileId,
  files,
  onFile,
  initialSegment,
  initialTime,
  initialShow,
}: {
  datasetId: number;
  fileId: number;
  files: LabelFile[];
  onFile: (id: number) => void;
  initialSegment: number | null;
  initialTime: number | null;
  initialShow: Show;
}) {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["annotate", fileId], queryFn: () => api.get<AnnotateView>(`/label/files/${fileId}`) });
  const m = useLabelMutations(fileId, datasetId);
  const ctl = useAudio(fileId);
  const [cur, setCur] = useState<number | null>(initialSegment);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [show, setShow] = useState<Show>(initialShow);
  const rowRefs = useRef(new Map<number, HTMLTableRowElement>());
  // The row whose text is in the editor, and the menu open on a row (a spelling hint's); Esc closes them.
  const [editing, setEditing] = useState<number | null>(null);
  const [menu, setMenu] = useState<{ id: number; hint: number } | null>(null);
  const listBox = useRef<HTMLDivElement>(null);

  const v = q.data;
  const segs = useMemo(() => v?.segments ?? [], [v]);
  const visible = useMemo(() => (show === "all" ? segs : segs.filter((s) => s.status === show)), [segs, show]);
  // Speaker lanes: every speaker seen on this file since it opened (a lane stays while you move its last
  // segment away) plus the ones added by hand, sorted; their order is also the 1–8 keys'.
  const [seen, setSeen] = useState<string[]>([]);
  const [added, setAdded] = useState<string[]>([]);
  useEffect(() => {
    if (v) setSeen((old) => (v.speakers.every((n) => old.includes(n)) ? old : [...new Set([...old, ...v.speakers])]));
  }, [v]);
  const speakers = useMemo(() => [...new Set([...seen, ...(v?.speakers ?? []), ...added])].sort(), [seen, v, added]);
  const colors = useMemo(() => speakerColors(speakers), [speakers]);
  const high = v?.thresholds.high ?? 0.85;
  const current = segs.find((s) => s.id === cur) ?? null;
  // The part of the file the timeline shows (zoom): the whole file until zoomed; follows playback and
  // the selected segment.
  const duration = v?.file.duration || 1;
  const [zoomed, setZoomed] = useState<Window | null>(null);
  const view = zoomed ?? fit(duration);
  const setView = useCallback((f: (w: Window) => Window) => setZoomed((w) => f(w ?? fit(duration))), [duration]);
  const zoomAround = useCallback(
    (factor: number) => setView((w) => zoomAt(w, factor, ctl.time >= w.t0 && ctl.time <= w.t1 ? ctl.time : (w.t0 + w.t1) / 2, duration)),
    [setView, ctl.time, duration],
  );
  const timelineBox = useRef<HTMLDivElement>(null);
  // A stretch pulled out on the waveform to listen to, across speakers; Space plays it while it is there.
  const [range, setRange] = useState<Window | null>(null);
  useWheelZoom(timelineBox, !!v, duration, setView);
  useEffect(() => {
    if (ctl.playing) setView((w) => follow(w, ctl.time, duration));
  }, [ctl.playing, ctl.time, duration, setView]);
  useEffect(() => {
    const s = segs.find((x) => x.id === cur);
    if (s) setView((w) => reveal(w, s.start, s.end, duration));
    // only when the selection changes, so a segment dragged out of sight does not pull the view after it
  }, [cur]);
  const active = useQuery({ queryKey: ["lexicons-active"], queryFn: () => api.get<{ lexicons: ActiveLexicon[] }>("/lexicons/active") });
  const lexicons = useMemo(() => active.data?.lexicons ?? [], [active.data]);
  const matcher = useMemo(() => new Matcher(lexicons), [lexicons]);
  const hints = useMemo(() => new Map(segs.map((s) => [s.id, matcher.hints(s.text, s.spelling_kept)])), [segs, matcher]);
  const hasHint = useCallback((s: Segment) => (hints.get(s.id)?.length ?? 0) > 0, [hints]);
  const applyHint = useCallback((s: Segment, h: Hint) => s.text !== null && m.patch.mutate({ id: s.id, text: houseSpelled(s.text, h) }), [m.patch]);
  const keepHint = useCallback((s: Segment, h: Hint) => m.patch.mutate({ id: s.id, spelling_kept: [...s.spelling_kept, norm(h.found)] }), [m.patch]);
  useEffect(() => setMenu(null), [segs]);

  // Pick a starting segment once loaded: the requested one, else the one at the requested time (and the
  // playhead goes there), else the first low-confidence one.
  const seeked = useRef(false);
  useEffect(() => {
    if (!v || (cur !== null && segs.some((s) => s.id === cur))) return;
    const atTime = initialTime !== null && !seeked.current ? segmentAt(segs, initialTime) : null;
    setCur(atTime ?? nextToReview(segs, null, 1, high) ?? segs[0]?.id ?? null);
  }, [v, segs, cur, high, initialTime]);
  useEffect(() => {
    if (!v || initialTime === null || seeked.current) return;
    seeked.current = true;
    ctl.seek(Math.max(0, Math.min(initialTime, v.file.duration)));
  }, [v, initialTime, ctl]);
  useEffect(() => {
    if (cur !== null) rowRefs.current.get(cur)?.scrollIntoView({ block: "nearest" });
  }, [cur]);
  // The segment under the playhead, highlighted on the lanes and in the list; during playback the list
  // scrolls just enough to keep its row in sight (the list alone, so the page stays where it is).
  const playingId = useMemo(() => playingSegment(segs, ctl.time), [segs, ctl.time]);
  useEffect(() => {
    const row = playingId !== null ? rowRefs.current.get(playingId) : undefined;
    const box = listBox.current;
    if (!ctl.playing || !row || !box) return;
    const r = row.getBoundingClientRect();
    const b = box.getBoundingClientRect();
    const head = box.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    if (r.top < b.top + head) box.scrollTop -= b.top + head - r.top;
    else if (r.bottom > b.bottom) box.scrollTop += r.bottom - b.bottom;
  }, [ctl.playing, playingId]);

  const targets = useCallback((): number[] => (sel.size ? [...sel] : cur !== null ? [cur] : []), [sel, cur]);
  const speakerForKey = (k: number) => speakers[k - 1] ?? `spk${k - 1}`;
  const nextId = (id: number) => {
    const k = segs.findIndex((s) => s.id === id);
    return segs[k + 1]?.id ?? null;
  };
  // The next segment to review after `from` (Tab, Shift+Tab, Ctrl+Enter); `done` were just confirmed.
  const toReview = useCallback(
    (from: number | null, direction: 1 | -1, done: number[] = []) =>
      nextToReview(done.length ? segs.map((s) => (done.includes(s.id) ? { ...s, status: "confirmed" as const, reviewed: true } : s)) : segs, from, direction, high, hasHint),
    [segs, high, hasHint],
  );
  const goTo = useCallback((id: number | null) => {
    if (id !== null) setCur(id);
  }, []);

  const confirm = useCallback(
    (ids: number[] = targets()) => {
      if (!ids.length) return;
      m.bulk.mutate({ ids, action: "confirm" });
      const from = cur ?? ids[0];
      goTo(toReview(from, 1, ids) ?? nextId(from) ?? from);
      setSel(new Set());
    },
    [targets, cur, segs, toReview, goTo],
  );

  // S and Split: the segment under the playhead, the selected one first where segments overlap.
  const splitTarget = useMemo(() => {
    const inside = (s: Segment) => ctl.time > s.start + 0.05 && ctl.time < s.end - 0.05;
    if (current && inside(current)) return current;
    const s = segs.find((x) => x.id === playingId);
    return s && inside(s) ? s : null;
  }, [current, segs, playingId, ctl.time]);
  const splitAtPlayhead = useCallback(() => {
    if (splitTarget) m.split.mutate({ id: splitTarget.id, t: ctl.time }, { onSuccess: ([a]) => setCur(a.id) });
  }, [splitTarget, ctl.time, m.split]);

  // Delete: the selected segment goes, and the next one (or the one before the last) is selected.
  const removeCurrent = useCallback(() => {
    if (!current) return;
    const k = segs.findIndex((s) => s.id === current.id);
    m.remove.mutate(current.id);
    setSel((old) => new Set([...old].filter((id) => id !== current.id)));
    setCur((segs[k + 1] ?? segs[k - 1])?.id ?? null);
  }, [current, segs, m.remove]);

  // "Undid merge": what the last undo or redo did, for a few seconds.
  const [undoNote, setUndoNote] = useState("");
  useEffect(() => {
    if (!undoNote) return;
    const t = window.setTimeout(() => setUndoNote(""), 4000);
    return () => window.clearTimeout(t);
  }, [undoNote]);
  const step = useCallback(
    (way: "undo" | "redo") => {
      if (m.history.isPending || !v?.history[way]) return;
      m.history.mutate(way, {
        onSuccess: ({ action, ids }) => {
          setUndoNote(`${way === "undo" ? "Undid" : "Redid"} ${action}`);
          setSel(new Set());
          if (ids.length) setCur(ids[0]);
        },
      });
    },
    [m.history, v],
  );

  const mergeSel = useCallback(() => {
    if (sel.size >= 2) {
      m.merge.mutate([...sel]);
      setSel(new Set());
    } else if (cur !== null && nextId(cur) !== null) {
      m.merge.mutate([cur, nextId(cur)!]);
    }
  }, [sel, cur, segs, m.merge]);

  useEffect(() => {
    // Tab and Shift+Tab are the page's on the page itself, the timeline and the segment list; elsewhere
    // (the header, the file picker, the bulk bar) Tab keeps its usual meaning.
    const tabHere = (t: EventTarget | null) =>
      t === document.body || (t instanceof Node && (!!timelineBox.current?.contains(t) || !!listBox.current?.contains(t)));
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && e.key === "Enter" && !isTyping(e)) {
        const h = current && hints.get(current.id)?.[0];
        if (current && h) {
          e.preventDefault();
          applyHint(current, h);
        }
        return;
      }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !isTyping(e)) {
        // outside text fields only, so the browser's own undo still works while you type
        const k = e.key.toLowerCase();
        if (k === "z" || k === "y") {
          e.preventDefault();
          step(k === "y" || e.shiftKey ? "redo" : "undo");
        } else if (e.key === "Enter") {
          e.preventDefault();
          confirm();
        }
        return;
      }
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === " ") {
        e.preventDefault();
        const span = range ? { start: range.t0, end: range.t1 } : current;
        if (e.shiftKey && span) ctl.play(span.start, span.end);
        else if (e.shiftKey) return;
        else if (ctl.playing) ctl.pause();
        else ctl.play(ctl.time >= duration - 0.05 ? 0 : ctl.time);
      } else if (e.key === "Tab") {
        if (!tabHere(e.target)) return;
        e.preventDefault();
        goTo(toReview(cur, e.shiftKey ? -1 : 1));
      } else if (/^[1-8]$/.test(e.key)) {
        const ids = targets();
        if (ids.length) m.bulk.mutate({ ids, action: "assign_speaker", speaker: speakerForKey(Number(e.key)) });
      } else if (e.key === "s" || e.key === "S") {
        splitAtPlayhead();
      } else if (e.key === "m" || e.key === "M") {
        mergeSel();
      } else if (e.key === "Delete") {
        removeCurrent();
      } else if (e.key === "Enter") {
        // a focused button or link keeps Enter
        if ((e.target as HTMLElement | null)?.closest?.("button, a") || !current) return;
        e.preventDefault();
        setEditing(current.id);
      } else if (e.key === "=" || e.key === "+") {
        zoomAround(STEP);
      } else if (e.key === "-" || e.key === "_") {
        zoomAround(1 / STEP);
      } else if (e.key === "0") {
        setView(() => fit(duration));
      } else if (e.key === "Escape") {
        if (menu) setMenu(null);
        else if (editing !== null) setEditing(null);
        else if (sel.size) setSel(new Set());
        else setRange(null);
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        ctl.skip(e.key === "ArrowLeft" ? -SKIP : SKIP);
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const k = visible.findIndex((s) => s.id === cur);
        const n = visible[Math.max(0, Math.min(visible.length - 1, k + (e.key === "ArrowDown" ? 1 : -1)))];
        if (n) {
          setCur(n.id);
          ctl.seek(n.start);
        }
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
  }, [ctl, current, segs, visible, cur, sel, menu, editing, targets, splitAtPlayhead, mergeSel, removeCurrent, confirm, toReview, goTo, speakers, hints, applyHint, zoomAround, setView, duration, range, step]);

  if (q.isLoading || !v) return <Spinner />;

  const live = segs.filter((s) => s.status !== "rejected");
  const lanes: Lane[] = [
    ...speakers.map((name, k) => ({ key: name, speaker: name, label: name, color: colors[name], added: added.includes(name) && !v.speakers.includes(name), hotkey: k < 8 ? k + 1 : undefined })),
    ...(live.some((s) => !s.speaker) ? [{ key: "", speaker: null, label: "no speaker", color: C.subtle }] : []),
  ];
  const autoCount = segs.filter((s) => s.status === "auto").length;
  const order = visible.map((s) => s.id);
  const fileIndex = files.findIndex((f) => f.id === fileId);

  return (
    <>
      <PageHeader
        crumbs={<Crumbs name={v.dataset.name} />}
        title={v.file.stem}
        subtitle={<span className="num">{v.file.path} · {clock(v.file.duration)} · file {v.position.index} of {v.position.of}</span>}
        actions={
          <>
            <Select aria-label="File" value={fileId} onChange={(e) => onFile(Number(e.target.value))}>
              {files.map((f) => (
                <option key={f.id} value={f.id} disabled={!f.usable}>
                  {f.stem} · {f.counts.review + f.counts.rejected} to review · {f.counts.confirmed} confirmed
                </option>
              ))}
            </Select>
            <Button disabled={!v.prev_file_id} onClick={() => v.prev_file_id && onFile(v.prev_file_id)} aria-label="Previous file">‹ Prev</Button>
            <Button disabled={!v.next_file_id} onClick={() => v.next_file_id && onFile(v.next_file_id)} aria-label="Next file">Next ›</Button>
          </>
        }
      />
      <LabelTabs datasetId={datasetId} current="annotate" />
      <div className="flex flex-wrap items-center gap-3">
        <span role="group" aria-label="Focus" className="inline-flex border border-line rounded-md overflow-hidden">
          <button type="button" aria-pressed className="min-h-8 px-3 text-xs border-0 bg-active text-fg">Transcript + speakers</button>
          <button type="button" disabled title="Keyword spotting is not built yet (Q6)" className="min-h-8 px-3 text-xs border-0 bg-transparent text-subtle cursor-not-allowed">Keywords</button>
          <button type="button" disabled title="Keyword spotting is not built yet (Q6)" className="min-h-8 px-3 text-xs border-0 bg-transparent text-subtle cursor-not-allowed">Both</button>
        </span>
        <span className="text-xs text-muted num">{fileIndex >= 0 ? `${fileIndex + 1}/${files.length}` : ""}</span>
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted" aria-live="polite">{undoNote}</span>
          <span role="group" aria-label="History" className="flex gap-1.5">
            <Button size="sm" disabled={!v.history.undo || m.history.isPending} title={v.history.undo ? `Undo ${v.history.undo} (Ctrl+Z)` : "Nothing to undo"} onClick={() => step("undo")}>
              Undo
            </Button>
            <Button size="sm" disabled={!v.history.redo || m.history.isPending} title={v.history.redo ? `Redo ${v.history.redo} (Ctrl+Shift+Z)` : "Nothing to redo"} onClick={() => step("redo")}>
              Redo
            </Button>
          </span>
          <Button size="sm" disabled={!autoCount || m.confirmAuto.isPending} onClick={() => m.confirmAuto.mutate()}>
            <I.Check size={13} /> Confirm all high-confidence ({autoCount})
          </Button>
          <Button size="sm" onClick={() => m.create.mutate({ start: Math.max(0, Math.min(ctl.time, v.file.duration - 1)), end: Math.min(v.file.duration, Math.max(ctl.time, 0) + 1), speaker: current?.speaker ?? null })}>
            <I.Plus size={13} /> Segment at playhead
          </Button>
        </span>
      </div>
      <ErrorNote error={m.error} />
      <Panel>
        <Transport
          ctl={ctl}
          duration={duration}
          range={range}
          onClearRange={() => setRange(null)}
          view={view}
          onView={(w) => setView(() => w)}
          split={{ onClick: splitAtPlayhead, disabled: !splitTarget, title: "Split the segment under the playhead (S)" }}
          merge={{ onClick: mergeSel, disabled: sel.size < 2 && (cur === null || nextId(cur) === null), title: "Merge the selected segments, or the selected segment with the next one (M)" }}
          remove={{ onClick: removeCurrent, disabled: !current, title: "Delete the selected segment (Delete)" }}
        />
        <div ref={timelineBox}>
        <Timeline
          fileId={fileId}
          t0={view.t0}
          t1={view.t1}
          time={ctl.time}
          markers={current ? [{ t0: current.start, t1: current.end, color: C.focus }] : []}
          selection={range}
          onSelection={(r) => {
            setRange(r);
            if (r) ctl.play(r.t0, r.t1);
          }}
          onSeek={(t) => {
            ctl.seek(t);
            const hit = segs.find((s) => s.start <= t && t <= s.end && s.status !== "rejected");
            if (hit) setCur(hit.id);
          }}
        />
        <SpeakerLanes
          duration={duration}
          view={view}
          selection={range}
          lanes={lanes}
          segs={live}
          currentId={cur}
          playingId={playingId}
          time={ctl.time}
          overlaps={overlapRegions(segs)}
          flags={segs.filter((s) => s.flagged)}
          note={speakerNote(segs, speakers, v.diariser)}
          canAdd={speakers.length < MAX_SPEAKERS}
          onAddSpeaker={() => setAdded((a) => [...a, nextSpeakerName(speakers)])}
          onSelect={setCur}
          onSeek={(t, id) => {
            ctl.seek(t);
            const hit = id ?? segs.find((s) => s.start <= t && t <= s.end && s.status !== "rejected")?.id;
            if (hit !== undefined) setCur(hit);
          }}
          onCommit={(id, edit) => m.move.mutateAsync({ id, ...edit })}
          onCreate={(edit) => m.create.mutate(edit, { onSuccess: (s) => setCur(s.id) })}
        />
        <Overview view={view} duration={duration} time={ctl.time} onView={(w) => setView(() => w)} />
        </div>
      </Panel>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel
          title={
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
              Segments · {num(segs.length)}
              {lexicons.length > 0 && <LexiconLink lexicons={lexicons} hints={[...hints.values()].reduce((a, h) => a + h.length, 0)} />}
            </span>
          }
          actions={
            <span role="group" aria-label="Show" className="inline-flex border border-line rounded-md overflow-hidden">
              {SHOW.map((o) => (
                <button key={o.value} type="button" aria-pressed={show === o.value} onClick={() => setShow(o.value)} className={cx("min-h-8 px-2.5 text-xs cursor-pointer border-0", show === o.value ? "bg-active text-fg" : "bg-transparent text-muted hover:text-fg")}>
                  {o.label}
                </button>
              ))}
            </span>
          }
        >
          {sel.size > 0 && (
            <BulkBar
              n={sel.size}
              speakers={speakers}
              onAssign={(spk) => m.bulk.mutate({ ids: [...sel], action: "assign_speaker", speaker: spk })}
              onAction={(action) => {
                m.bulk.mutate({ ids: [...sel], action });
                setSel(new Set());
              }}
              onMerge={() => {
                m.merge.mutate([...sel]);
                setSel(new Set());
              }}
              onClear={() => setSel(new Set())}
            />
          )}
          {visible.length === 0 && <Empty>No segments {show === "all" ? "in this file" : `with status “${STATUS_LABELS[show as SegStatus]}”`}.</Empty>}
          {visible.length > 0 && (
            <div ref={listBox} className="overflow-auto scroll-thin max-h-[60vh] border border-track rounded-md">
              <table className="w-full border-collapse text-[13px]" style={{ minWidth: 720 }}>
                <thead className="sticky top-0 z-10 bg-panel">
                  <tr className="text-[11px] text-muted uppercase tracking-wider text-left">
                    <th scope="col" className="px-2 py-2 border-b border-line w-8">
                      <input
                        type="checkbox"
                        aria-label="Select all shown"
                        checked={sel.size > 0 && order.every((id) => sel.has(id))}
                        onChange={(e) => setSel(e.target.checked ? new Set(order) : new Set())}
                      />
                    </th>
                    <th scope="col" className="px-2 py-2 border-b border-line w-8" />
                    <th scope="col" className="px-2 py-2 border-b border-line font-medium">Time</th>
                    <th scope="col" className="px-2 py-2 border-b border-line font-medium">Speaker</th>
                    <th scope="col" className="px-2 py-2 border-b border-line font-medium">Transcript</th>
                    <th scope="col" className="px-2 py-2 border-b border-line font-medium text-right">Conf.</th>
                    <th scope="col" className="px-2 py-2 border-b border-line font-medium">Status</th>
                    <th scope="col" className="px-2 py-2 border-b border-line w-20" />
                  </tr>
                </thead>
                <tbody>
                  {visible.map((s) => (
                    <SegmentRow
                      key={s.id}
                      s={s}
                      rowRef={(el) => {
                        if (el) rowRefs.current.set(s.id, el);
                        else rowRefs.current.delete(s.id);
                      }}
                      current={s.id === cur}
                      playing={s.id === playingId}
                      checked={sel.has(s.id)}
                      color={s.speaker ? colors[s.speaker] ?? C.subtle : C.subtle}
                      high={high}
                      low={v.thresholds.low}
                      ctl={ctl}
                      onFocus={() => setCur(s.id)}
                      onCheck={(shift) => {
                        setSel(selectRange(order, sel, s.id, anchor, shift));
                        setAnchor(s.id);
                      }}
                      onSpeaker={() => m.patch.mutate({ id: s.id, speaker: cycleSpeaker(speakers.length ? speakers : ["spk0"], s.speaker) })}
                      onText={(text) => m.patch.mutate({ id: s.id, text })}
                      hints={hints.get(s.id) ?? []}
                      onUseHint={(h) => applyHint(s, h)}
                      onKeepHint={(h) => keepHint(s, h)}
                      onConfirm={() => m.bulk.mutate({ ids: [s.id], action: "confirm" })}
                      onReject={() => m.bulk.mutate({ ids: [s.id], action: "reject" })}
                      editing={editing === s.id}
                      onEdit={(on) => setEditing((e) => (on ? s.id : e === s.id ? null : e))}
                      menu={menu?.id === s.id ? menu.hint : null}
                      onMenu={(k) => setMenu(k === null ? null : { id: s.id, hint: k })}
                      onNext={(direction) => goTo(toReview(s.id, direction))}
                      onConfirmNext={() => confirm([s.id])}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Shortcuts />
        </Panel>
        <SidePanel
          s={current}
          ctl={ctl}
          onUse={(text) => current && m.patch.mutate({ id: current.id, text })}
          onFlag={() => current && m.patch.mutate({ id: current.id, flagged: !current.flagged })}
          onLive={() => navigate(`/label/live/${datasetId}`)}
        />
      </div>
    </>
  );
}

/** `text` split around its hints: plain strings, and the index of each hint in between. */
function pieces(text: string, hints: Hint[]): (string | number)[] {
  const out: (string | number)[] = [];
  let at = 0;
  hints.forEach((h, k) => {
    if (h.start > at) out.push(text.slice(at, h.start));
    out.push(k);
    at = h.end;
  });
  if (at < text.length) out.push(text.slice(at));
  return out;
}

/** In the segments header: the lexicons in use and how many hints they give in this file. */
function LexiconLink({ lexicons, hints }: { lexicons: ActiveLexicon[]; hints: number }) {
  const one = lexicons.length === 1 ? lexicons[0] : null;
  return (
    <Link to={one ? `/lexicons/${one.id}` : "/lexicons"} className="inline-flex items-center gap-2 text-xs font-normal !text-muted hover:!text-fg no-underline">
      <I.Book size={14} />
      {one ? <>{one.name} <span className="num">v{one.version}</span></> : <>{lexicons.length} lexicons</>} · <span className="num">{num(hints)}</span> spelling hint{hints === 1 ? "" : "s"}
    </Link>
  );
}

/** Under the lanes: where the speakers came from, e.g. "2 detected by pyannote-diarization · 1 added by you". */
function speakerNote(segs: Segment[], lanes: string[], diariser: string | null): string {
  const fromRun = new Set(segs.filter((s) => s.source_run_id !== null && s.speaker).map((s) => s.speaker!));
  const fromRefs = new Set(segs.filter((s) => s.source_run_id === null && !s.created && s.speaker && !fromRun.has(s.speaker)).map((s) => s.speaker!));
  const byYou = lanes.filter((n) => !fromRun.has(n) && !fromRefs.has(n)).length;
  const parts = [
    fromRun.size ? `${fromRun.size} detected by ${diariser ?? "pseudo-labelling"}` : "",
    fromRefs.size ? `${fromRefs.size} from the references` : "",
    byYou ? `${byYou} added by you` : "",
    lanes.length >= MAX_SPEAKERS ? `limit of ${MAX_SPEAKERS} reached` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

function BulkBar({ n, speakers, onAssign, onAction, onMerge, onClear }: { n: number; speakers: string[]; onAssign: (s: string) => void; onAction: (a: string) => void; onMerge: () => void; onClear: () => void }) {
  const [spk, setSpk] = useState("");
  return (
    <div role="toolbar" aria-label="Bulk actions" className="flex flex-wrap items-center gap-2 bg-active/60 border border-line rounded-md px-3 py-2">
      <span className="text-xs num">{n} selected</span>
      <label className="flex items-center gap-1.5 text-xs text-muted">
        Speaker
        <input list="speaker-names" value={spk} onChange={(e) => setSpk(e.target.value)} placeholder="name" className="bg-canvas border border-line rounded px-2 min-h-8 w-28 text-fg text-xs" />
        <datalist id="speaker-names">{speakers.map((s) => <option key={s} value={s} />)}</datalist>
        <Button size="sm" disabled={!spk.trim()} onClick={() => onAssign(spk.trim())}>Assign</Button>
      </label>
      <Button size="sm" disabled={n < 2} onClick={onMerge}>Merge</Button>
      <Button size="sm" variant="primary" onClick={() => onAction("confirm")}><I.Check size={12} /> Confirm</Button>
      <Button size="sm" variant="danger" onClick={() => onAction("reject")}><I.X size={12} /> Reject</Button>
      <Button size="sm" onClick={() => onAction("flag")}><I.Flag size={12} /> Flag</Button>
      <Button size="sm" variant="ghost" onClick={onClear} className="ml-auto">Clear</Button>
    </div>
  );
}

function SegmentRow({
  s,
  rowRef,
  current,
  playing,
  checked,
  color,
  high,
  low,
  ctl,
  onFocus,
  onCheck,
  onSpeaker,
  onText,
  hints,
  onUseHint,
  onKeepHint,
  onConfirm,
  onReject,
  editing,
  onEdit,
  menu,
  onMenu,
  onNext,
  onConfirmNext,
}: {
  s: Segment;
  rowRef: (el: HTMLTableRowElement | null) => void;
  current: boolean;
  /** Under the playhead. */
  playing: boolean;
  checked: boolean;
  color: string;
  high: number;
  low: number;
  ctl: ReturnType<typeof useAudio>;
  onFocus: () => void;
  onCheck: (shift: boolean) => void;
  onSpeaker: () => void;
  onText: (t: string) => void;
  hints: Hint[];
  onUseHint: (h: Hint) => void;
  onKeepHint: (h: Hint) => void;
  onConfirm: () => void;
  onReject: () => void;
  /** The text is in the editor (Enter, a click, or the pen with spelling hints). */
  editing: boolean;
  onEdit: (on: boolean) => void;
  /** The spelling hint whose menu is open. */
  menu: number | null;
  onMenu: (k: number | null) => void;
  /** Tab and Shift+Tab in the editor: save, then go to the next or previous segment to review. */
  onNext: (direction: 1 | -1) => void;
  /** Ctrl+Enter in the editor: save, confirm, and go to the next segment to review. */
  onConfirmNext: () => void;
}) {
  const [draft, setDraft] = useState(s.text ?? "");
  // With spelling hints the transcript shows as text with the variants underlined; a click elsewhere edits it.
  const open = menu;
  const setOpen = onMenu;
  useEffect(() => setDraft(s.text ?? ""), [s.text]);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) input.current?.focus();
  }, [editing]);
  // Set while a key closes the editor, so the blur that follows doesn't save (or cancel) a second time.
  const closing = useRef(false);
  const save = () => {
    if (draft !== (s.text ?? "")) onText(draft);
  };
  const close = (keep: boolean) => {
    if (keep) save();
    else setDraft(s.text ?? "");
    closing.current = true;
    input.current?.blur();
    closing.current = false;
    onEdit(false);
  };
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      close(true);
      onConfirmNext();
    } else if (e.key === "Enter" && !e.altKey) {
      e.preventDefault();
      close(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close(false);
    } else if (e.key === "Tab") {
      e.preventDefault();
      close(true);
      onNext(e.shiftKey ? -1 : 1);
    }
  };
  return (
    <tr ref={rowRef} onClick={onFocus} aria-current={current || undefined} data-playing={playing || undefined} className={cx("border-b border-track", current ? "bg-active" : playing ? "bg-focus/10" : "hover:bg-track/60", s.status === "rejected" && "opacity-60")}>
      <td className={cx("px-2 py-1.5", playing && "shadow-[inset_3px_0_0_var(--color-focus)]")}>
        <input
          type="checkbox"
          aria-label={`Select segment at ${clock(s.start)}`}
          checked={checked}
          onChange={() => undefined}
          onClick={(e) => {
            e.stopPropagation();
            onCheck(e.shiftKey);
          }}
        />
      </td>
      <td className="px-2 py-1.5">
        <PlayFrom ctl={ctl} t={s.start} until={s.end} label={`Play segment at ${clock(s.start)}`} />
      </td>
      <td className="px-2 py-1.5 num text-xs text-muted whitespace-nowrap">
        {clock(s.start)}
        <br />
        {clock(s.end)}
      </td>
      <td className="px-2 py-1.5">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onSpeaker();
          }}
          title="Click to cycle speakers (or press 1–8)"
          className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs cursor-pointer bg-transparent text-fg num whitespace-nowrap"
          style={{ borderColor: color }}
        >
          <span className="size-2 rounded-full" style={{ background: color }} />
          {s.speaker ?? "—"}
        </button>
      </td>
      <td className="px-2 py-1.5 w-full">
        {hints.length > 0 && !editing && s.text !== null ? (
          <div className="flex flex-col gap-1.5">
            <div onClick={() => onEdit(true)} className="group/t px-1.5 py-1 rounded border border-transparent hover:border-line text-fg text-[13px] cursor-text">
              {pieces(s.text, hints).map((p, k) =>
                typeof p === "string" ? (
                  <span key={k}>{p}</span>
                ) : (
                  <button
                    key={k}
                    type="button"
                    aria-expanded={open === p}
                    aria-label={`${hints[p].found}: house spelling is ${hints[p].word}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onFocus();
                      setOpen(open === p ? null : p);
                    }}
                    className="p-0 border-0 bg-transparent text-fg text-[13px] cursor-pointer underline decoration-wavy decoration-warn underline-offset-[3px]"
                  >
                    {hints[p].found}
                  </button>
                ),
              )}
              <button
                type="button"
                aria-label={`Edit transcript at ${clock(s.start)}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onEdit(true);
                }}
                className="ml-1.5 p-0 border-0 bg-transparent text-subtle hover:text-fg cursor-pointer align-middle opacity-0 group-hover/t:opacity-100 focus-visible:opacity-100"
              >
                <I.Pen size={12} />
              </button>
            </div>
            {open !== null && hints[open] && (
              <div role="group" aria-label="Spelling hint" className="flex flex-wrap items-center gap-2 px-2 py-1.5 rounded-md border border-line bg-canvas text-xs text-muted" onClick={(e) => e.stopPropagation()}>
                House spelling <span className="num text-fg">{hints[open].word}</span>
                <span className="text-subtle">· {hints[open].lexicon}</span>
                <span className="ml-auto flex gap-1.5">
                  <Button size="sm" variant="primary" onClick={() => onUseHint(hints[open])}>Use</Button>
                  <Button size="sm" onClick={() => onKeepHint(hints[open])}>Keep</Button>
                </span>
              </div>
            )}
          </div>
        ) : (
          <input
            ref={input}
            aria-label={`Transcript at ${clock(s.start)}`}
            value={draft}
            placeholder={s.text === null ? "(no transcript)" : ""}
            onFocus={() => {
              onFocus();
              onEdit(true);
            }}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              if (closing.current) return;
              save();
              onEdit(false);
            }}
            onKeyDown={onKey}
            className="w-full bg-transparent border border-transparent hover:border-line focus:border-focus rounded px-1.5 py-1 text-fg text-[13px] outline-none"
          />
        )}
      </td>
      <td className="px-2 py-1.5 text-right num text-xs font-semibold" style={{ color: confidenceColor(s.confidence, high, low) }}>
        {s.confidence === null ? "—" : s.confidence.toFixed(2)}
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        <Chip tone={STATUS_TONE[s.status]}>{statusLabel(s)}</Chip>
        {s.flagged && <span className="ml-1 text-warn" title={s.flag_note ?? "Flagged"}><I.Flag size={12} /></span>}
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        <span className="flex gap-1">
          <button type="button" aria-label="Confirm" title="Confirm (Ctrl+Enter)" onClick={(e) => { e.stopPropagation(); onConfirm(); }} className="size-7 inline-flex items-center justify-center rounded border border-line bg-transparent text-good cursor-pointer hover:bg-track">
            <I.Check size={12} />
          </button>
          <button type="button" aria-label="Reject" title="Reject" onClick={(e) => { e.stopPropagation(); onReject(); }} className="size-7 inline-flex items-center justify-center rounded border border-line bg-transparent text-bad cursor-pointer hover:bg-track">
            <I.X size={12} />
          </button>
        </span>
      </td>
    </tr>
  );
}

function SidePanel({ s, ctl, onUse, onFlag, onLive }: { s: Segment | null; ctl: ReturnType<typeof useAudio>; onUse: (t: string) => void; onFlag: () => void; onLive: () => void }) {
  if (!s) return <Panel title="Segment"><span className="text-[13px] text-muted">Select a segment.</span></Panel>;
  return (
    <Panel title="Segment" subtitle={<span className="num">{clock(s.start)} – {clock(s.end)} · {(s.end - s.start).toFixed(2)} s</span>}>
      <div className="flex flex-col gap-2">
        <span className="text-[11px] text-muted uppercase tracking-wider">Model outputs</span>
        {!s.alternatives?.length && <span className="text-[13px] text-muted">None stored for this span{s.reviewed ? " (edited or created by hand)" : ""}.</span>}
        {s.alternatives?.map((a) => (
          <div key={a.service} className="flex flex-col gap-1 bg-canvas border border-track rounded-md p-2">
            <span className="flex items-center justify-between gap-2 text-xs">
              <span className="truncate">
                {a.model}
                {s.chosen === a.service && <span className="ml-1.5 text-primary">· starting hypothesis</span>}
                {a.source_app && <span className="ml-1.5 text-subtle" title={`output read from ${a.source_app} through the Model Catalog's index`}>· from {a.source_app === "bench" ? "Concord Bench" : a.source_app}</span>}
                {!a.build_key && <span className="ml-1.5 text-subtle" title="made before the Model Catalog: not reused by new runs">· older build</span>}
              </span>
              {s.agreement?.[a.service] !== undefined && <span className="num text-muted">agrees {(100 * s.agreement[a.service]).toFixed(0)}%</span>}
            </span>
            <span className="text-[13px]">{a.text || <span className="text-subtle">(empty)</span>}</span>
            <Button size="sm" variant="ghost" className="self-start" disabled={a.text === s.text} onClick={() => onUse(a.text)}>Use this text</Button>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => ctl.play(s.start, s.end)}><I.Play size={11} /> Play</Button>
        <Button size="sm" variant={s.flagged ? "danger" : "outline"} onClick={onFlag} aria-pressed={s.flagged}><I.Flag size={12} /> {s.flagged ? "Unflag" : "Flag"}</Button>
      </div>
      <span className="text-xs text-muted">
        {s.reviewed ? "Reviewed: pseudo-labelling will not replace this segment." : "Not reviewed yet: a new pseudo-label run may replace it."}
      </span>
      <div className="flex flex-wrap gap-2 pt-2 border-t border-track">
        <Button size="sm" variant="ghost" onClick={onLive}><I.Pulse size={12} /> Live evaluation</Button>
      </div>
    </Panel>
  );
}

export function Shortcuts() {
  return (
    <span className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted" aria-label="Keyboard shortcuts">
      {SHORTCUTS.map(({ key, short }) => (
        <span key={key} className="flex items-center gap-1.5">
          <kbd className="num text-[10px] border border-line rounded px-1 py-px text-fg bg-track">{key}</kbd>
          {short}
        </span>
      ))}
      <Link to="/label" className="ml-auto text-[11px]">Back to the console</Link>
    </span>
  );
}
