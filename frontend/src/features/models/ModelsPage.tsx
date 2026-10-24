import { useQuery } from "@tanstack/react-query";
import { api, type Model, type ModelList } from "../../lib/api";
import { capLabel, coldStart, timingLabel } from "../../lib/format";
import { Chip, Empty, ErrorNote, PageHeader, Panel, Spinner, StatusDot } from "../../components/ui";

// Models live in the Model Catalog: it registers, verifies, starts and serves them. This page lists them
// read-only, grouped by what the catalog's verifier found they do (a model doing two shows in both
// groups); registering a model or reading its verification report happens in the catalog's own UI.

const GROUPS: { key: Model["groups"][number] | "other"; title: string; subtitle: string; empty: string }[] = [
  { key: "asr", title: "ASR", subtitle: "Transcribe speech. The ensemble members of pseudo-labelling.", empty: "No ASR models in the catalog." },
  { key: "seg", title: "VAD / Diarisation", subtitle: "Find speech and who is speaking. The first stage of pseudo-labelling.", empty: "No VAD or diarisation models in the catalog." },
  { key: "enhance", title: "Speech enhancement", subtitle: "Clean up noisy or reverberant audio. Listed from the catalog; pseudo-labelling does not use them.", empty: "No speech enhancement models in the catalog." },
  { key: "other", title: "Other", subtitle: "Not verified yet, verification failed, or a task none of the groups above covers.", empty: "" },
];

export function ModelsPage() {
  const q = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelList>("/models"), refetchInterval: 10_000 });
  const cat = q.data?.catalog;
  const models = q.data?.models ?? [];
  return (
    <>
      <PageHeader
        crumbs="Setup"
        title="Models"
        subtitle="From the Model Catalog, which registers, verifies and runs them. Capabilities and timing come from its verifier."
        actions={cat && <a href={cat.ui_url} target="_blank" rel="noreferrer" className="text-[13px]">Open the Model Catalog ↗</a>}
      />
      {q.isLoading && <Spinner />}
      <ErrorNote error={q.error} />
      {cat && !cat.reachable && <ErrorNote error={`The Model Catalog at ${cat.url} is not reachable (${cat.error}). Showing the models Label has used, as last seen.`} />}
      {q.data && models.length === 0 && (
        <Panel>
          <Empty>
            No models in the catalog yet. Register them in the <a href={cat?.ui_url} target="_blank" rel="noreferrer">Model Catalog</a>.
          </Empty>
        </Panel>
      )}
      {models.length > 0 &&
        GROUPS.map((g) => {
          const these = models.filter((m) => (g.key === "other" ? m.groups.length === 0 : m.groups.includes(g.key)));
          if (g.key === "other" && these.length === 0) return null;
          return (
            <Panel key={g.key} id={`models-${g.key}`} title={<>{g.title} <span className="num text-muted font-normal">· {these.length}</span></>} subtitle={g.subtitle}>
              {these.length === 0 ? <Empty>{g.empty}</Empty> : <ModelTable models={these} group={g.key} caption={`${g.title} models`} />}
            </Panel>
          );
        })}
    </>
  );
}

/** One group's models. Fixed column widths, so the columns line up from one group's table to the next. */
function ModelTable({ models, group, caption }: { models: Model[]; group: string; caption: string }) {
  return (
    <div className="overflow-x-auto scroll-thin">
      <table className="w-full min-w-[880px] table-fixed border-collapse text-[13px]">
        <caption className="sr-only">{caption}</caption>
        <colgroup>
          <col className="w-[30%]" />
          <col className="w-[10%]" />
          <col className="w-[18%]" />
          <col className="w-[11%]" />
          <col className="w-[10%]" />
          <col className="w-[13%]" />
          <col className="w-[8%]" />
        </colgroup>
        <thead>
          <tr className="text-[11px] text-muted uppercase tracking-wider text-left">
            <th scope="col" className="font-medium px-3 py-2 border-b border-line">Model</th>
            <th scope="col" className="font-medium px-3 py-2 border-b border-line">State</th>
            <th scope="col" className="font-medium px-3 py-2 border-b border-line">Capabilities</th>
            <th scope="col" className="font-medium px-3 py-2 border-b border-line">Timing</th>
            <th scope="col" className="font-medium px-3 py-2 border-b border-line">Cold start</th>
            <th scope="col" className="font-medium px-3 py-2 border-b border-line">Build</th>
            <th scope="col" className="font-medium px-3 py-2 border-b border-line text-right whitespace-nowrap">Label runs</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.catalog_id} className={`border-b border-track align-top ${m.stages.length || group === "enhance" ? "" : "text-muted"}`}>
              <td className="px-3 py-2.5">
                <span className="flex flex-col">
                  <a href={m.catalog_url} target="_blank" rel="noreferrer">{m.display_name}</a>
                  <span className="text-[11px] text-subtle num">{m.catalog_id} · {m.contract} / {m.api}</span>
                  {m.tags.length > 0 && <span className="flex flex-wrap gap-1 mt-1">{m.tags.map((t) => <Chip key={t}>#{t}</Chip>)}</span>}
                </span>
              </td>
              <td className="px-3 py-2.5">
                <span className="flex items-center gap-1.5 text-xs" title={m.state_detail ?? (m.leased_by.length ? `leased by ${m.leased_by.join(", ")}` : undefined)}>
                  <StatusDot state={m.state} />
                  {m.state}
                </span>
              </td>
              <td className="px-3 py-2.5">
                {m.capabilities.length > 0 ? (
                  <span className="flex flex-wrap gap-1">{m.capabilities.map((c) => <Chip key={c} tone="good">{capLabel(c)}</Chip>)}</span>
                ) : group === "enhance" ? (
                  <Chip>Speech enhancement</Chip>
                ) : (
                  <span className="text-xs text-subtle">{m.not_usable ?? "—"}</span>
                )}
                {m.capabilities_status === "stale" && <span className="block text-[11px] text-warn mt-1">verified on an earlier build</span>}
              </td>
              <td className="px-3 py-2.5 text-xs text-muted">{timingLabel(m.timing_level)}</td>
              <td className="px-3 py-2.5 text-xs text-muted num">{coldStart(m.cold_start_s, m.cold_start_measured)}</td>
              <td className="px-3 py-2.5 text-[11px] text-muted num" title={m.image ?? undefined}>{m.build_key ?? "not known yet"}</td>
              <td className="px-3 py-2.5 text-right num">{m.runs}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
