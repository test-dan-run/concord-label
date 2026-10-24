// Publish to Dataset Catalog (DATASET_CATALOG_PLAN.md §8.2): a dataset's confirmed segments as a new version in the
// Dataset Catalog, which replaced Send to Bench (contract version 3; set up with DATASET_STORE). The audio is in the
// catalog's store already, and model outputs are in the Model Catalog's output index, so there is nothing to choose
// about either. When someone published after the version this dataset is based on, the panel asks first.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError, qs, type CatalogPublishState, type Job, type ReferenceExport, type ReferencePreview } from "../../lib/api";
import { useConfig } from "../../lib/config";
import { ago, hours, num } from "../../lib/format";
import { TagInput } from "../../components/TagInput";
import * as I from "../../components/icons";
import { Button, ErrorNote, Fact, JobPill, Panel, Select } from "../../components/ui";
import { DownloadPackage, useReferenceExport } from "./ReferencePackages";

const SCOPE_LABELS = { complete: "Fully reviewed files (ASR + DER)", any: "Any file with confirmed segments (ASR only)" };
const REFS_LABELS: Record<string, string> = {
  "segments.seglst.json": "speaker + text (SegLST)",
  "segments.jsonl": "text (JSONL)",
  "refs.rttm": "speakers (RTTM)",
};

/** Publish to Dataset Catalog when the catalog is set up, else a reference package to download. */
export function PublishOrDownload({ datasetId, className }: { datasetId: number; className?: string }) {
  const cfg = useConfig();
  if (!cfg.data) return null;
  return cfg.data.dataset_catalog.enabled ? <PublishToCatalog datasetId={datasetId} className={className} /> : <DownloadPackage datasetId={datasetId} className={className} />;
}

function when(iso: string | null | undefined): string {
  if (!iso) return "an unknown time";
  return `${new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleString()} (${ago(iso)})`;
}

export function PublishToCatalog({ datasetId, className }: { datasetId: number; className?: string }) {
  const qc = useQueryClient();
  const [scope, setScope] = useState<"complete" | "any">("complete");
  const [name, setName] = useState<string | null>(null);
  const [tags, setTags] = useState<string[] | null>(null);
  const [force, setForce] = useState(false);
  const [sent, setSent] = useState<number | null>(null);
  const pv = useQuery({
    queryKey: ["reference-preview", datasetId, scope],
    queryFn: () => api.get<ReferencePreview>(`/datasets/${datasetId}/reference-preview${qs({ scope })}`),
  });
  const p = pv.data;
  const cat = p?.catalog ?? null;
  useEffect(() => {
    if (p && name === null) setName(p.catalog?.name ?? p.dataset.name);
    if (p && tags === null) setTags(p.dataset.tags);
  }, [p, name, tags]);
  const go = useMutation({
    mutationFn: () =>
      api.post<{ export: ReferenceExport; job: Job }>(`/datasets/${datasetId}/catalog-publishes`, {
        name: (name ?? "").trim(),
        scope,
        tags: tags ?? [],
        force,
      }),
    onSuccess: (r) => {
      setSent(r.export.id);
      setForce(false);
      void qc.invalidateQueries({ queryKey: ["reference-exports", datasetId] });
    },
    onError: (e) => {
      // someone published meanwhile: the preview then says who, and asks
      if (e instanceof ApiError && e.status === 409) void qc.invalidateQueries({ queryKey: ["reference-preview", datasetId] });
    },
  });
  const rec = useReferenceExport(sent);
  const finished = rec.data && ["done", "failed"].includes(rec.data.status);
  useEffect(() => {
    if (finished) {
      void qc.invalidateQueries({ queryKey: ["reference-exports", datasetId] });
      void qc.invalidateQueries({ queryKey: ["reference-preview", datasetId] });
      void qc.invalidateQueries({ queryKey: ["dataset", String(datasetId)] });
    }
  }, [finished, qc, datasetId]);
  const busy = go.isPending || (rec.data !== undefined && !finished);
  const blocked = !p || !!p.problem || !(name ?? "").trim() || busy || !!cat?.error || (!!cat?.stale && !force);
  return (
    <Panel title="Publish to Dataset Catalog" subtitle="Confirmed segments only, as a new version in the Dataset Catalog. The audio is in its store already." className={className}>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Name
        <input value={name ?? ""} onChange={(e) => setName(e.target.value)} className="bg-canvas border border-line rounded px-2 min-h-9 text-fg text-[13px]" />
      </label>
      <Select label="Files" value={scope} onChange={(e) => setScope(e.target.value as "complete" | "any")}>
        <option value="complete">{SCOPE_LABELS.complete}</option>
        <option value="any">{SCOPE_LABELS.any}</option>
      </Select>
      <TagInput value={tags ?? []} onChange={setTags} />
      <ErrorNote error={pv.error} />
      {p && (
        <div className="flex flex-col gap-0.5 border-t border-track pt-2">
          <Fact k="Files" v={<>{num(p.files)}{p.left_out > 0 && <span className="text-muted"> · {num(p.left_out)} left out (not fully reviewed)</span>}</>} />
          <Fact k="Segments" v={`${num(p.segments)} · ${hours(p.hours)}`} />
          <Fact k="References" v={p.refs_file ? REFS_LABELS[p.refs_file] ?? p.refs_file : "—"} />
          <Fact k="Labels for slicing" v={p.labels.length ? p.labels.join(", ") : "none (no file metadata)"} />
          {cat && <CatalogFacts cat={cat} />}
          {p.problem && <span className="text-xs text-warn">{p.problem}</span>}
        </div>
      )}
      {cat?.error && <ErrorNote error={cat.error} />}
      {cat?.stale && cat.latest && (
        <div role="alert" className="flex flex-col gap-1.5 rounded border border-warn/50 bg-warn/10 p-2.5 text-[13px]">
          <span>
            <a href={cat.latest.url} target="_blank" rel="noreferrer">v{cat.latest.n}</a> was published by {cat.latest.published_by ?? "someone"} on {when(cat.latest.published_at)}, after{" "}
            {cat.based_on ? `v${cat.based_on}, the version this dataset is based on` : "this dataset was linked to the catalog"}. Publishing now makes a version that leaves out its changes.
          </span>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            Publish anyway
          </label>
        </div>
      )}
      <ErrorNote error={go.error} />
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={blocked} onClick={() => go.mutate()}>
          <I.Upload size={14} /> Publish to Dataset Catalog
        </Button>
        {rec.data && !finished && <JobPill status={rec.data.status} />}
      </div>
      {rec.data && finished && <PublishResult r={rec.data} />}
    </Panel>
  );
}

function CatalogFacts({ cat }: { cat: CatalogPublishState }) {
  if (!cat.dataset_id || cat.deleted) {
    return <Fact k="Catalog dataset" v={cat.deleted ? "deleted in the catalog; publishing makes a new one" : "new, made on the first publish"} />;
  }
  return (
    <>
      <Fact k="Catalog dataset" v={<a href={cat.url ?? undefined} target="_blank" rel="noreferrer">{cat.name ?? cat.dataset_id} ↗</a>} />
      <Fact k="Based on" v={cat.based_on ? `v${cat.based_on}` : "no version yet"} />
      {cat.latest && <Fact k="Latest version" v={`v${cat.latest.n} by ${cat.latest.published_by ?? "?"}, ${ago(cat.latest.published_at)}`} />}
    </>
  );
}

function PublishResult({ r }: { r: ReferenceExport }) {
  if (r.status === "failed") return <ErrorNote error={`Publishing failed: ${r.error}`} />;
  return (
    <span className="text-[13px]">
      {r.counts.created === false ? "Same content as " : "Published "}
      <a href={r.catalog_url ?? undefined} target="_blank" rel="noreferrer">v{r.version}</a>
      {r.counts.created === false ? "; nothing new was published." : <> in the Dataset Catalog ({num(r.counts.files)} files, {num(r.counts.segments)} segments{r.counts.uploaded ? `, ${num(r.counts.uploaded)} files uploaded to the store` : ""}).</>}
    </span>
  );
}
