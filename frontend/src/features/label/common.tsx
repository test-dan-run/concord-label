// Shared pieces of the Label screens: the stacked progress bar, the per-dataset tab strip, the
// dataset chooser shown when a Label screen is opened without a dataset, the switch between the two ways of
// labelling a dataset, and what Annotate's two views have in common.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, useSearchParams } from "react-router-dom";
import { api, type ConsoleRow, type Dataset, type LabelProgress, type Labelling, type Segment } from "../../lib/api";
import { hours, num } from "../../lib/format";
import { BAR_COLORS, BAR_LABELS, BAR_ORDER, SHORTCUTS } from "../../lib/label";
import type { ActiveLexicon } from "../../lib/lexicon";
import * as I from "../../components/icons";
import { Button, cx, Empty, ErrorNote, PageHeader, Panel, Spinner } from "../../components/ui";

export function useConsole() {
  return useQuery({
    queryKey: ["label-console"],
    queryFn: () => api.get<ConsoleRow[]>("/label/console"),
    refetchInterval: (q) => (q.state.data?.some((r) => r.progress.running) ? 2000 : false),
  });
}

/** Stacked bar in audio time: confirmed / auto-accepted / needs review / rejected / running / not labelled. */
export function StatusBar({ p, className }: { p: LabelProgress; className?: string }) {
  const total = p.seconds || 1;
  const parts = BAR_ORDER.filter((k) => p.bar[k] > 0);
  const label = BAR_ORDER.map((k) => `${BAR_LABELS[k]} ${Math.round((100 * p.bar[k]) / total)}%`).join(", ");
  return (
    <span role="img" aria-label={label} title={label} className={cx("flex h-2 w-full rounded overflow-hidden bg-track", className)}>
      {parts.map((k) => (
        <span key={k} className={cx("h-full", k === "running" && "animate-pulse")} style={{ width: `${(100 * p.bar[k]) / total}%`, background: BAR_COLORS[k] }} />
      ))}
    </span>
  );
}

export function BarLegend({ p }: { p?: LabelProgress }) {
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
      {BAR_ORDER.map((k) => (
        <span key={k} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: BAR_COLORS[k] }} />
          {BAR_LABELS[k]}
          {p && p.seconds > 0 && <span className="num text-subtle">{Math.round((100 * p.bar[k]) / p.seconds)}%</span>}
        </span>
      ))}
    </span>
  );
}

/** Pseudo-label · Annotate · Live evaluation for one dataset. */
export function LabelTabs({ datasetId, current }: { datasetId: number; current: "pseudo" | "annotate" | "live" }) {
  const items: [typeof current, string, string][] = [
    ["pseudo", "Pseudo-label", `/label/pseudo/${datasetId}`],
    ["annotate", "Annotate", `/label/annotate/${datasetId}`],
    ["live", "Live evaluation", `/label/live/${datasetId}`],
  ];
  return (
    <nav aria-label="Label screens" className="flex gap-1 border-b border-track">
      {items.map(([k, label, to]) => (
        <NavLink
          key={k}
          to={to}
          aria-current={k === current ? "page" : undefined}
          className={cx("px-3 pb-2.5 pt-1 text-[13px] border-b-2 no-underline", k === current ? "border-primary !text-fg font-medium" : "border-transparent !text-muted hover:!text-fg")}
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

export function Crumbs({ name }: { name?: string }) {
  return (
    <>
      <Link to="/label" className="!text-muted hover:!text-fg">Label</Link>
      {name && <> · {name}</>}
    </>
  );
}

/** A Label screen opened from the nav, with no dataset yet: pick one. */
export function DatasetChooser({ title, to, subtitle }: { title: string; to: (id: number) => string; subtitle: string }) {
  const q = useConsole();
  return (
    <>
      <PageHeader crumbs={<Crumbs />} title={title} subtitle={subtitle} />
      <Panel title="Choose a dataset">
        {q.isLoading && <Spinner />}
        <ErrorNote error={q.error} />
        {q.data && q.data.length === 0 && <Empty>No datasets yet. <Link to="/datasets/upload">Upload one</Link>.</Empty>}
        <ul className="m-0 p-0 list-none flex flex-col">
          {(q.data ?? []).map((r) => (
            <li key={r.id} className="border-b border-track last:border-0">
              <Link to={to(r.id)} className="grid grid-cols-[minmax(0,1fr)_minmax(120px,240px)] items-center gap-4 px-2 py-2.5 no-underline !text-fg hover:bg-track rounded">
                <span className="flex flex-col min-w-0">
                  <span className="truncate">{r.name}</span>
                  <span className="text-xs text-muted">
                    {r.label_source} · <span className="num">{hours(r.hours)}</span> · <span className="num">{num(r.progress.segments.review)}</span> to review
                  </span>
                </span>
                <StatusBar p={r.progress} />
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </>
  );
}

const LABELLINGS: { value: Labelling; label: string; title: string }[] = [
  { value: "clips", label: "One transcript per clip", title: "Each clip is one utterance: no speakers, and pseudo-labelling transcribes the whole clip." },
  { value: "turns", label: "Speaker turns", title: "A VAD or diariser finds the turns in each file, and each turn has a speaker and a transcript." },
];

/** How the dataset is labelled, which picks Annotate's view and what pseudo-labelling runs. Nothing in the segments
 * changes when you switch. On a clip dataset opened with `?view=speakers`, it also leads back to the clip view. */
export function LabellingSwitch({ datasetId }: { datasetId: number }) {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const key = ["dataset", String(datasetId)];
  const ds = useQuery({ queryKey: key, queryFn: () => api.get<Dataset>(`/datasets/${datasetId}`) });
  const change = useMutation({
    mutationFn: (labelling: Labelling) => api.patch<Dataset>(`/datasets/${datasetId}`, { labelling }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: key });
      void qc.invalidateQueries({ queryKey: ["label-results", datasetId] });
      void qc.invalidateQueries({ queryKey: ["label-preview", datasetId] });
      void qc.invalidateQueries({ queryKey: ["label-console"] });
    },
  });
  const override = params.get("view") === "speakers";
  const current = override ? "turns" : ds.data?.labelling;
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span role="group" aria-label="How this dataset is labelled" className="inline-flex border border-line rounded-md overflow-hidden">
        {LABELLINGS.map((o) => (
          <button
            key={o.value}
            type="button"
            title={o.title}
            aria-pressed={current === o.value}
            disabled={!ds.data || change.isPending}
            onClick={() => {
              if (current === o.value) return;
              if (override) {
                const p = new URLSearchParams(params);
                p.delete("view");
                setParams(p);
              }
              if (ds.data?.labelling !== o.value) change.mutate(o.value);
            }}
            className={cx("min-h-8 px-3 text-xs cursor-pointer border-0", current === o.value ? "bg-active text-fg" : "bg-transparent text-muted hover:text-fg")}
          >
            {o.label}
          </button>
        ))}
      </span>
      <ErrorNote error={change.error} />
    </span>
  );
}

/** How many segments of the dataset hold an uploaded label, and how many wait for a person. Under "label-files", so
 * an edit in either view of Annotate counts them again. */
export function useUploaded(datasetId: number) {
  return useQuery({ queryKey: ["label-files", datasetId, "uploaded"], queryFn: () => api.get<{ labels: number; waiting: number }>(`/label/datasets/${datasetId}/uploaded`) });
}

/** "Keep all uploaded labels": for labels you trust as they are, so you needn't check them one at a time. */
export function KeepUploaded({ datasetId }: { datasetId: number }) {
  const qc = useQueryClient();
  const waiting = useUploaded(datasetId).data?.waiting ?? 0;
  const keep = useMutation({
    mutationFn: () => api.post(`/label/datasets/${datasetId}/confirm-uploaded`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["label-clips", datasetId] });
      void qc.invalidateQueries({ queryKey: ["label-files", datasetId] });
      void qc.invalidateQueries({ queryKey: ["annotate"] });
      void qc.invalidateQueries({ queryKey: ["label-console"] });
      void qc.invalidateQueries({ queryKey: ["label-results", datasetId] });
    },
  });
  if (!waiting) return null;
  return (
    <span className="flex items-center gap-2">
      <Button
        size="sm"
        disabled={keep.isPending}
        title="Confirm every uploaded label that nobody has checked, as it was uploaded"
        onClick={() => {
          if (window.confirm(`Keep ${num(waiting)} uploaded label${waiting === 1 ? "" : "s"} without listening to them? Each one replaces any pseudo-label in its place and becomes a reference. You can only undo this one file at a time.`)) keep.mutate();
        }}
      >
        Keep all uploaded labels ({num(waiting)})
      </Button>
      <ErrorNote error={keep.error} />
    </span>
  );
}

const NOT_TEXT = new Set(["checkbox", "radio", "button", "submit", "reset", "range", "file", "color"]);

/** Whether a key goes to a field that takes text (a checkbox or a button does not: the shortcuts apply). */
export function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  if (t.tagName === "INPUT") return !NOT_TEXT.has((t as HTMLInputElement).type);
  return t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable;
}

/** In a list's header: the lexicons in use and how many hints they give. */
export function LexiconLink({ lexicons, hints }: { lexicons: ActiveLexicon[]; hints: number }) {
  const one = lexicons.length === 1 ? lexicons[0] : null;
  return (
    <Link to={one ? `/lexicons/${one.id}` : "/lexicons"} className="inline-flex items-center gap-2 text-xs font-normal !text-muted hover:!text-fg no-underline">
      <I.Book size={14} />
      {one ? <>{one.name} <span className="num">v{one.version}</span></> : <>{lexicons.length} lexicons</>} · <span className="num">{num(hints)}</span> spelling hint{hints === 1 ? "" : "s"}
    </Link>
  );
}

/** Each member's text for a segment, with a button to use it. */
export function ModelOutputs({ s, onUse }: { s: Segment; onUse: (t: string) => void }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-[11px] text-muted uppercase tracking-wider">Model outputs</span>
      {!s.alternatives?.length && <span className="text-[13px] text-muted">None stored for this span{s.reviewed && s.uploaded_text === null ? " (edited or created by hand)" : ""}.</span>}
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
  );
}

export function Shortcuts({ list = SHORTCUTS }: { list?: { key: string; short: string }[] }) {
  return (
    <span className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted" aria-label="Keyboard shortcuts">
      {list.map(({ key, short }) => (
        <span key={key} className="flex items-center gap-1.5">
          <kbd className="num text-[10px] border border-line rounded px-1 py-px text-fg bg-track">{key}</kbd>
          {short}
        </span>
      ))}
      <Link to="/label" className="ml-auto text-[11px]">Back to the console</Link>
    </span>
  );
}
