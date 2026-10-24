// Send to Bench (SPLIT_PLAN.md §4.2, CONTRACTS.md §2): a dataset's confirmed segments as a reference package,
// pushed to Concord Bench when BENCH_URL answers with our contract version, else built as a zip to download.
// Also the history of sends and the import of a flags file from Bench (CONTRACTS.md §3, offline case).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, qs, type FlagsImport, type Job, type ReferenceExport, type ReferencePreview } from "../../lib/api";
import { ago, bytes, hours, num } from "../../lib/format";
import { TagInput } from "../../components/TagInput";
import * as I from "../../components/icons";
import { Button, Chip, Empty, ErrorNote, Fact, JobPill, Panel, Select } from "../../components/ui";

const SCOPE_LABELS = { complete: "Fully reviewed files (ASR + DER)", any: "Any file with confirmed segments (ASR only)" };
const REFS_LABELS: Record<string, string> = {
  "segments.seglst.json": "speaker + text (SegLST)",
  "segments.jsonl": "text (JSONL)",
  "refs.rttm": "speakers (RTTM)",
};

export function useReferenceExport(id: number | null) {
  return useQuery({
    queryKey: ["reference-export", id],
    queryFn: () => api.get<ReferenceExport>(`/reference-exports/${id}`),
    enabled: id !== null,
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : 1000),
  });
}

export function SendToBench({ datasetId, className }: { datasetId: number; className?: string }) {
  const qc = useQueryClient();
  const [scope, setScope] = useState<"complete" | "any">("complete");
  const [name, setName] = useState<string | null>(null);
  const [tags, setTags] = useState<string[] | null>(null);
  const [audio, setAudio] = useState("auto");
  const [outputs, setOutputs] = useState(false);
  const [sent, setSent] = useState<number | null>(null);
  const pv = useQuery({
    queryKey: ["reference-preview", datasetId, scope],
    queryFn: () => api.get<ReferencePreview>(`/datasets/${datasetId}/reference-preview${qs({ scope })}`),
  });
  const p = pv.data;
  useEffect(() => {
    if (p && name === null) setName(p.dataset.name);
    if (p && tags === null) setTags(p.dataset.tags);
  }, [p, name, tags]);
  const go = useMutation({
    mutationFn: () =>
      api.post<{ export: ReferenceExport; job: Job }>(`/datasets/${datasetId}/reference-exports`, {
        name: (name ?? "").trim(),
        scope,
        tags: tags ?? [],
        include_audio: audio,
        include_outputs: outputs,
      }),
    onSuccess: (r) => {
      setSent(r.export.id);
      void qc.invalidateQueries({ queryKey: ["reference-exports", datasetId] });
    },
  });
  const rec = useReferenceExport(sent);
  const finished = rec.data && ["done", "failed"].includes(rec.data.status);
  useEffect(() => {
    if (finished) {
      void qc.invalidateQueries({ queryKey: ["reference-exports", datasetId] });
      void qc.invalidateQueries({ queryKey: ["reference-preview", datasetId] });
    }
  }, [finished, qc, datasetId]);
  const push = p?.destination === "push";
  const busy = go.isPending || (rec.data !== undefined && !finished);
  return (
    <Panel title="Send to Bench" subtitle="Confirmed segments only, as a reference package for Concord Bench." className={className}>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Package name
        <input value={name ?? ""} onChange={(e) => setName(e.target.value)} className="bg-canvas border border-line rounded px-2 min-h-9 text-fg text-[13px]" />
      </label>
      <Select label="Files" value={scope} onChange={(e) => setScope(e.target.value as "complete" | "any")}>
        <option value="complete">{SCOPE_LABELS.complete}</option>
        <option value="any">{SCOPE_LABELS.any}</option>
      </Select>
      <TagInput value={tags ?? []} onChange={setTags} />
      <Select label="Include audio" value={audio} onChange={(e) => setAudio(e.target.value)}>
        <option value="auto">{push ? "Auto: only files Bench does not have" : "Auto: all files (download)"}</option>
        <option value="always">Always: every file</option>
        <option value="never">Never: Bench must already have every file</option>
      </Select>
      <label className="flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={outputs} onChange={(e) => setOutputs(e.target.checked)} />
        Include model outputs (the segmenter's, so Bench need not run it again)
      </label>
      <ErrorNote error={pv.error} />
      {p && (
        <div className="flex flex-col gap-0.5 border-t border-track pt-2">
          <Fact k="Files" v={<>{num(p.files)}{p.left_out > 0 && <span className="text-muted"> · {num(p.left_out)} left out (not fully reviewed)</span>}</>} />
          <Fact k="Segments" v={`${num(p.segments)} · ${hours(p.hours)}`} />
          <Fact k="References" v={p.refs_file ? REFS_LABELS[p.refs_file] ?? p.refs_file : "—"} />
          <Fact k="Labels for slicing" v={p.labels.length ? p.labels.join(", ") : "none (no file metadata)"} />
          <Fact k="Version" v={`v${p.next_version}`} />
          <Fact
            k="Destination"
            v={push ? <span title={p.peer.url ?? ""}>Push to Concord Bench at {p.peer.url}</span> : <span>Download (zip)</span>}
          />
          {!push && p.peer.url && <span className="text-xs text-warn">{p.push_refused}; the package is built for download.</span>}
          {p.problem && <span className="text-xs text-warn">{p.problem}</span>}
        </div>
      )}
      <ErrorNote error={go.error} />
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={!p || !!p.problem || !(name ?? "").trim() || busy} onClick={() => go.mutate()}>
          {push ? <I.Upload size={14} /> : <I.Download size={14} />} Send to Bench
        </Button>
        {rec.data && !finished && <JobPill status={rec.data.status} />}
      </div>
      {rec.data && finished && <SendResult r={rec.data} />}
    </Panel>
  );
}

function SendResult({ r }: { r: ReferenceExport }) {
  if (r.status === "failed") return <ErrorNote error={`Send to Bench failed: ${r.error}`} />;
  return (
    <div className="flex flex-col gap-1 text-[13px]">
      {r.destination === "push" && (
        <span>
          Staged in Concord Bench as reference import <span className="num">#{r.bench_import_id}</span>: accept it there
          {r.bench_url && <> (<a href={r.bench_url} target="_blank" rel="noreferrer">{r.bench_url}</a>)</>}.
        </span>
      )}
      {r.download && (
        <a href={r.download} download>
          Download {r.file} <span className="num text-muted">({bytes(Number(r.counts.bytes ?? 0))}, {num(r.counts.files)} files, {num(r.counts.segments)} segments)</span>
        </a>
      )}
    </div>
  );
}

export function SendHistory({ datasetId }: { datasetId: number }) {
  const q = useQuery({
    queryKey: ["reference-exports", datasetId],
    queryFn: () => api.get<ReferenceExport[]>(`/datasets/${datasetId}/reference-exports`),
    refetchInterval: (query) => ((query.state.data ?? []).some((r) => r.status === "queued" || r.status === "running") ? 1500 : false),
  });
  const rows = q.data ?? [];
  return (
    <Panel title="Sent to Bench" subtitle="Each send is a new version of this dataset in Concord Bench.">
      <ErrorNote error={q.error} />
      {rows.length === 0 && <Empty>Nothing sent yet.</Empty>}
      {rows.length > 0 && (
        <div className="overflow-x-auto scroll-thin">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr className="text-[11px] text-muted uppercase tracking-wider text-left">
                <th scope="col" className="font-medium px-2 py-2 border-b border-line">Version</th>
                <th scope="col" className="font-medium px-2 py-2 border-b border-line">Scope</th>
                <th scope="col" className="font-medium px-2 py-2 border-b border-line text-right">Files</th>
                <th scope="col" className="font-medium px-2 py-2 border-b border-line text-right">Segments</th>
                <th scope="col" className="font-medium px-2 py-2 border-b border-line">Destination</th>
                <th scope="col" className="font-medium px-2 py-2 border-b border-line">Status</th>
                <th scope="col" className="font-medium px-2 py-2 border-b border-line" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-track align-top">
                  <td className="px-2 py-1.5 num">{r.version ? `v${r.version}` : "—"}</td>
                  <td className="px-2 py-1.5">{r.scope}</td>
                  <td className="px-2 py-1.5 num text-right">{num(r.counts.files)}</td>
                  <td className="px-2 py-1.5 num text-right">{num(r.counts.segments)}</td>
                  <td className="px-2 py-1.5">
                    {r.destination === "push" ? (
                      <span>
                        Bench{r.bench_import_id !== null && <span className="num"> · import #{r.bench_import_id}</span>}
                        {r.bench_url && <span className="block text-xs text-muted truncate max-w-[220px]"><a href={r.bench_url} target="_blank" rel="noreferrer">{r.bench_url}</a></span>}
                      </span>
                    ) : (
                      "download"
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    <Chip tone={r.status === "done" ? "good" : r.status === "failed" ? "bad" : "info"}>{r.status}</Chip>
                    <span className="block text-[11px] text-muted">{ago(r.finished_at ?? r.created_at)}</span>
                    {r.error && <span className="block text-xs text-bad max-w-[260px]">{r.error}</span>}
                  </td>
                  <td className="px-2 py-1.5">{r.download && <a href={r.download} download className="text-xs">Download</a>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** A flags file from Concord Bench (CONTRACTS.md §3), for when Bench cannot reach this install. */
export function ImportFlags({ datasetId }: { datasetId: number }) {
  const qc = useQueryClient();
  const go = useMutation({
    mutationFn: async (f: File) => {
      let body: unknown;
      try {
        body = JSON.parse(await f.text());
      } catch {
        throw new Error(`${f.name} is not a JSON flags file`);
      }
      return api.post<FlagsImport>("/flags/import", body);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["annotate"] });
      void qc.invalidateQueries({ queryKey: ["label-files"] });
    },
  });
  return (
    <Panel title="Flags from Bench" subtitle="References flagged as wrong in Concord Bench show in the annotator's flags lane.">
      <label className="flex flex-col gap-1 text-xs text-muted">
        Flags file (.json)
        <input type="file" accept=".json,application/json" onChange={(e) => e.target.files?.[0] && go.mutate(e.target.files[0])} className="text-[13px] text-fg" />
      </label>
      <ErrorNote error={go.error} />
      {go.data && (
        <span className="text-[13px]">
          {num(go.data.matched)} flag{go.data.matched === 1 ? "" : "s"} matched, {num(go.data.unmatched)} unmatched
          {go.data.dataset_id !== datasetId && <> (dataset {go.data.dataset})</>}.{" "}
          <Link to={`/label/annotate/${go.data.dataset_id}`}>Open the annotator</Link>
        </span>
      )}
    </Panel>
  );
}
