// New from Dataset Catalog (DATASET_CATALOG_PLAN.md §8.3): pick a catalog dataset and one of its versions, and Label
// makes a working dataset from it. The files point at the audio in the catalog's store, the references become
// confirmed segments with their uids, and a version with audio only makes an audio-only dataset to pseudo-label.

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, qs, type CatalogDataset, type CatalogVersion, type Dataset, type Job } from "../../lib/api";
import { useConfig } from "../../lib/config";
import { ago, hours, num } from "../../lib/format";
import { TagInput } from "../../components/TagInput";
import { Button, Chip, cx, Empty, ErrorNote, Fact, PageHeader, Panel, Select, Spinner } from "../../components/ui";

function labelChips(types: string[]) {
  if (!types.length) return <Chip>audio only</Chip>;
  return (
    <>
      {types.includes("asr") && <Chip>ASR</Chip>}
      {types.includes("diar") && <Chip>Diarisation</Chip>}
    </>
  );
}

function versionText(v: CatalogVersion): string {
  const what = v.label_types.length ? v.label_types.map((t) => (t === "asr" ? "ASR" : "diarisation")).join(" + ") : "audio only";
  return `v${v.n} · ${num(v.counts.files)} files · ${what} · ${v.published_by ?? "?"}, ${ago(v.published_at)}`;
}

export function NewFromCatalog() {
  const cfg = useConfig();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["catalog-datasets", q],
    queryFn: () => api.get<CatalogDataset[]>(`/dataset-catalog/datasets${qs({ q })}`),
    enabled: cfg.data?.dataset_catalog.enabled === true,
  });
  const detail = useQuery({
    queryKey: ["catalog-dataset", picked],
    queryFn: () => api.get<CatalogDataset>(`/dataset-catalog/datasets/${picked}`),
    enabled: picked !== null,
  });
  const [version, setVersion] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const d = detail.data;
  useEffect(() => {
    if (d) {
      setVersion(d.versions?.[0]?.n ?? null);
      setName(d.name);
      setTags(d.tags);
    }
  }, [d]);
  const make = useMutation({
    mutationFn: () =>
      api.post<{ dataset: Dataset; job: Job }>("/datasets/from-catalog", { catalog_dataset_id: picked, version, name: name.trim(), tags }),
    onSuccess: (r) => nav(`/datasets/${r.dataset.id}`),
  });

  const crumbs = <><Link to="/datasets">Datasets</Link> / New from Dataset Catalog</>;
  if (cfg.data && !cfg.data.dataset_catalog.enabled) {
    return (
      <>
        <PageHeader crumbs={crumbs} title="New from Dataset Catalog" />
        <Panel>
          <Empty>The Dataset Catalog is not set up. Set DATASET_STORE to its store to use it.</Empty>
        </Panel>
      </>
    );
  }
  const chosen = d?.versions?.find((v) => v.n === version) ?? null;
  return (
    <>
      <PageHeader
        crumbs={crumbs}
        title="New from Dataset Catalog"
        subtitle="Make a working dataset from a published version. The audio stays in the catalog's store, and the references become confirmed segments."
        actions={cfg.data?.dataset_catalog.ui_url && <a href={cfg.data.dataset_catalog.ui_url} target="_blank" rel="noreferrer" className="text-[13px]">Open the Dataset Catalog ↗</a>}
      />
      <div className="flex flex-col xl:flex-row gap-5 items-start">
        <Panel title="Catalog datasets" className="flex-1 w-full">
          <label className="flex flex-col gap-1 text-xs text-muted max-w-sm">
            Search
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="name, description or tag" className="bg-canvas border border-line rounded px-2 min-h-9 text-fg text-[13px]" />
          </label>
          {list.isLoading && <Spinner />}
          <ErrorNote error={list.error} />
          {list.data && list.data.length === 0 && <Empty>No datasets in the Dataset Catalog{q ? " match" : " yet"}.</Empty>}
          {list.data && list.data.length > 0 && (
            <div className="overflow-x-auto scroll-thin">
              <table className="w-full border-collapse text-[13px]">
                <caption className="sr-only">Datasets in the Dataset Catalog</caption>
                <thead>
                  <tr className="text-[11px] text-muted uppercase tracking-wider text-left">
                    <th scope="col" className="font-medium px-3 py-2 border-b border-line">Name</th>
                    <th scope="col" className="font-medium px-3 py-2 border-b border-line">Latest</th>
                    <th scope="col" className="font-medium px-3 py-2 border-b border-line">Labels</th>
                    <th scope="col" className="font-medium px-3 py-2 border-b border-line text-right">Files</th>
                    <th scope="col" className="font-medium px-3 py-2 border-b border-line text-right">Audio</th>
                    <th scope="col" className="font-medium px-3 py-2 border-b border-line">Published</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.map((x) => (
                    <tr key={x.id} aria-selected={x.id === picked} className={cx("border-b border-track", x.id === picked ? "bg-track" : "hover:bg-track/50")}>
                      <td className="px-3 py-2">
                        <button type="button" onClick={() => setPicked(x.id)} className="bg-transparent border-0 p-0 text-left text-focus cursor-pointer">
                          {x.name}
                        </button>
                        {x.tags.length > 0 && <span className="ml-2 inline-flex flex-wrap gap-1">{x.tags.map((t) => <Chip key={t}>#{t}</Chip>)}</span>}
                      </td>
                      <td className="px-3 py-2 num">{x.latest ? `v${x.latest.n}` : "—"}</td>
                      <td className="px-3 py-2"><span className="flex gap-1">{x.latest ? labelChips(x.latest.label_types) : "—"}</span></td>
                      <td className="px-3 py-2 num text-right">{num(x.latest?.counts.files)}</td>
                      <td className="px-3 py-2 num text-right">{hours(x.latest?.counts.hours)}</td>
                      <td className="px-3 py-2 text-xs text-muted">{x.latest ? `${x.latest.published_by ?? "?"}, ${ago(x.latest.published_at)}` : "no versions"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <aside className="w-full xl:w-[380px] shrink-0 flex flex-col gap-4">
          <Panel title="New dataset" subtitle={d ? undefined : "Pick a catalog dataset on the left."}>
            {picked && detail.isLoading && <Spinner />}
            <ErrorNote error={detail.error} />
            {d && (
              <>
                <Fact k="Catalog dataset" v={<a href={d.url} target="_blank" rel="noreferrer">{d.name} ↗</a>} />
                {d.description && <span className="text-xs text-muted">{d.description}</span>}
                {(d.versions ?? []).length === 0 ? (
                  <Empty>This dataset has no versions yet.</Empty>
                ) : (
                  <>
                    <Select label="Version" className="min-w-0 flex-1" value={version ?? ""} onChange={(e) => setVersion(Number(e.target.value))}>
                      {(d.versions ?? []).map((v) => (
                        <option key={v.n} value={v.n}>
                          {versionText(v)}
                        </option>
                      ))}
                    </Select>
                    {chosen && (
                      <div className="flex flex-col gap-0.5">
                        <Fact k="Labels" v={<span className="flex gap-1">{labelChips(chosen.label_types)}</span>} />
                        <Fact k="Segments" v={num(chosen.counts.segments)} />
                        <Fact k="Audio" v={hours(chosen.counts.hours)} />
                        {!chosen.label_types.length && <span className="text-xs text-muted">Audio only: the new dataset is ready to pseudo-label.</span>}
                      </div>
                    )}
                    <label className="flex flex-col gap-1 text-xs text-muted">
                      Name
                      <input value={name} onChange={(e) => setName(e.target.value)} className="bg-canvas border border-line rounded px-2 min-h-9 text-fg text-[13px]" />
                    </label>
                    <TagInput value={tags} onChange={setTags} />
                    <ErrorNote error={make.error} />
                    <Button variant="primary" disabled={!version || !name.trim() || make.isPending} onClick={() => make.mutate()}>
                      Make dataset
                    </Button>
                  </>
                )}
              </>
            )}
          </Panel>
        </aside>
      </div>
    </>
  );
}
