import { useQuery } from "@tanstack/react-query";
import { api, type ModelList } from "../../lib/api";
import { capLabel, coldStart, timingLabel } from "../../lib/format";
import { Chip, Empty, ErrorNote, PageHeader, Panel, Spinner, StatusDot } from "../../components/ui";

// Models live in the model catalog: it registers, verifies, starts and serves them. This page lists them
// read-only; registering a model or reading its verification report happens in the catalog's own UI.
export function ModelsPage() {
  const q = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelList>("/models"), refetchInterval: 10_000 });
  const cat = q.data?.catalog;
  const models = q.data?.models ?? [];
  return (
    <>
      <PageHeader
        crumbs="Setup"
        title="Models"
        subtitle="From the model catalog, which registers, verifies and runs them. Capabilities and timing come from its verifier."
        actions={cat && <a href={cat.ui_url} target="_blank" rel="noreferrer" className="text-[13px]">Open the model catalog ↗</a>}
      />
      <Panel>
        {q.isLoading && <Spinner />}
        <ErrorNote error={q.error} />
        {cat && !cat.reachable && <ErrorNote error={`The model catalog at ${cat.url} is not reachable (${cat.error}). Showing the models Label has used, as last seen.`} />}
        {q.data && models.length === 0 && (
          <Empty>
            No models in the catalog yet. Register them in the <a href={cat?.ui_url} target="_blank" rel="noreferrer">model catalog</a>.
          </Empty>
        )}
        {models.length > 0 && (
          <div className="overflow-x-auto scroll-thin">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="text-[11px] text-muted uppercase tracking-wider text-left">
                  <th scope="col" className="font-medium px-3 py-2 border-b border-line">Model</th>
                  <th scope="col" className="font-medium px-3 py-2 border-b border-line">State</th>
                  <th scope="col" className="font-medium px-3 py-2 border-b border-line">Capabilities</th>
                  <th scope="col" className="font-medium px-3 py-2 border-b border-line">Timing</th>
                  <th scope="col" className="font-medium px-3 py-2 border-b border-line">Cold start</th>
                  <th scope="col" className="font-medium px-3 py-2 border-b border-line">Build</th>
                  <th scope="col" className="font-medium px-3 py-2 border-b border-line text-right">Label runs</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.catalog_id} className={`border-b border-track align-top ${m.stages.length ? "" : "text-muted"}`}>
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
        )}
      </Panel>
    </>
  );
}
