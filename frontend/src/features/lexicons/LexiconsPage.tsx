// Setup › Lexicons: house spellings for words written more than one way. Every lexicon applies to every
// dataset. Entries are edited as a draft; Annotate keeps using the published version until the next publish.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type LexiconDetail, type LexiconSummary } from "../../lib/api";
import { ago, num } from "../../lib/format";
import * as I from "../../components/icons";
import { Button, cx, Empty, ErrorNote, PageHeader, Panel, Spinner } from "../../components/ui";

const input = "min-h-9 bg-panel border border-line rounded-md px-2 text-[13px] text-fg num";
const plural = (n: number, one: string, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;

export function LexiconsPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["lexicons"], queryFn: () => api.get<LexiconSummary[]>("/lexicons") });
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: (n: string) => api.post<LexiconDetail>("/lexicons", { name: n }),
    onSuccess: (lx) => {
      setCreating(false);
      setName("");
      void qc.invalidateQueries({ queryKey: ["lexicons"] });
      navigate(`/lexicons/${lx.id}`);
    },
  });
  const selected = id ? Number(id) : list.data?.[0]?.id ?? null;

  return (
    <>
      <PageHeader
        crumbs="Setup"
        title="Lexicons"
        subtitle="House spellings for words annotators write in more than one way. In Annotate, a variant is underlined with a one-click fix."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <I.Plus size={14} /> New lexicon
          </Button>
        }
      />
      <div className="flex flex-wrap gap-5 items-start">
        <nav aria-label="Lexicons" className="flex-[1_1_260px] min-w-0 bg-panel border border-track rounded-lg p-2 flex flex-col gap-1">
          {creating && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) create.mutate(name.trim());
              }}
              className="flex flex-col gap-2 p-2 border-b border-track mb-1"
            >
              <label className="flex flex-col gap-1.5 text-xs text-muted">
                Name
                <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Singlish particles" className={input} />
              </label>
              <ErrorNote error={create.error} />
              <span className="flex gap-2">
                <Button type="submit" size="sm" variant="primary" disabled={!name.trim() || create.isPending}>Create</Button>
                <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
              </span>
            </form>
          )}
          {list.isLoading && <Spinner />}
          <ErrorNote error={list.error} />
          {list.data?.length === 0 && !creating && <Empty>No lexicons yet.</Empty>}
          {list.data?.map((lx) => (
            <Link
              key={lx.id}
              to={`/lexicons/${lx.id}`}
              aria-current={lx.id === selected ? "true" : undefined}
              className={cx("flex flex-col gap-1 px-3 py-2.5 rounded-md no-underline !text-fg", lx.id === selected ? "bg-active" : "hover:bg-track")}
            >
              <span className="flex items-center gap-2 text-[13px] font-medium">
                <span className="truncate">{lx.name}</span>
                <span className="ml-auto num text-[11px] text-muted whitespace-nowrap">{lx.version ? `v${lx.version}` : "not published"}</span>
              </span>
              <span className="text-xs text-muted">
                {plural(lx.entries, "entry", "entries")} · {lx.datasets ? plural(lx.datasets, "dataset") : "not in use"}
                {lx.dirty && <span className="text-warn"> · unpublished changes</span>}
              </span>
            </Link>
          ))}
        </nav>
        <div className="flex-[999_1_560px] min-w-0">{selected !== null && <LexiconPanel key={selected} id={selected} />}</div>
      </div>
    </>
  );
}

function LexiconPanel({ id }: { id: number }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["lexicon", id], queryFn: () => api.get<LexiconDetail>(`/lexicons/${id}`) });
  const done = (lx?: LexiconDetail) => {
    if (lx) qc.setQueryData(["lexicon", id], lx);
    void qc.invalidateQueries({ queryKey: ["lexicons"] });
    void qc.invalidateQueries({ queryKey: ["lexicons-active"] });
  };
  const add = useMutation({ mutationFn: (b: { word: string; variants: string[] }) => api.post<LexiconDetail>(`/lexicons/${id}/entries`, b), onSuccess: done });
  const remove = useMutation({ mutationFn: (word: string) => api.post<LexiconDetail>(`/lexicons/${id}/entries/remove`, { word }), onSuccess: done });
  const publish = useMutation({ mutationFn: () => api.post<LexiconDetail>(`/lexicons/${id}/publish`), onSuccess: done });
  const discard = useMutation({ mutationFn: () => api.post<LexiconDetail>(`/lexicons/${id}/discard`), onSuccess: done });
  const del = useMutation({
    mutationFn: () => api.del(`/lexicons/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["lexicons"] });
      void qc.invalidateQueries({ queryKey: ["lexicons-active"] });
      navigate("/lexicons");
    },
  });
  const [word, setWord] = useState("");
  const [variants, setVariants] = useState("");

  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorNote error={q.error} />;
  const lx = q.data;
  const next = `v${(lx.version ?? 0) + 1}`;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!word.trim()) return;
    add.mutate(
      { word: word.trim(), variants: variants.split(",").map((v) => v.trim()).filter(Boolean) },
      { onSuccess: () => { setWord(""); setVariants(""); } },
    );
  };

  return (
    <Panel
      id="lex-title"
      title={<span className="text-base">{lx.name}</span>}
      subtitle={
        <>
          {lx.description && <>{lx.description} </>}
          {lx.version ? <>Published <span className="num">v{lx.version}</span> · {ago(lx.published_at)}.</> : "Not published yet."} Applies to every dataset.
        </>
      }
      actions={
        <Button size="sm" variant="ghost" onClick={() => window.confirm(`Delete the lexicon “${lx.name}” and all its versions? Annotate stops showing its hints.`) && del.mutate()}>
          Delete
        </Button>
      }
    >
      <ErrorNote error={add.error ?? remove.error ?? publish.error ?? discard.error ?? del.error} />
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2.5 p-3 bg-canvas border border-track rounded-md">
        <label className="flex-[1_1_160px] flex flex-col gap-1.5 text-xs text-muted">
          House spelling
          <input value={word} onChange={(e) => setWord(e.target.value)} placeholder="e.g. lah" className={input} />
        </label>
        <label className="flex-[2_1_240px] flex flex-col gap-1.5 text-xs text-muted">
          Variants to flag (comma-separated)
          <input value={variants} onChange={(e) => setVariants(e.target.value)} placeholder="e.g. la, laa" className={input} />
        </label>
        <Button type="submit" disabled={!word.trim() || add.isPending} className="!min-h-9">
          <I.Plus size={13} /> Add entry
        </Button>
      </form>
      {lx.problems.length > 0 && (
        <div role="alert" className="flex flex-col gap-1 p-3 rounded-md border border-warn/50 text-xs text-warn">
          <span className="font-medium">Fix these before publishing:</span>
          {lx.problems.map((p) => <span key={p}>{p}</span>)}
        </div>
      )}
      {lx.draft.length === 0 ? (
        <Empty>No entries yet. Add a house spelling and the variants to flag.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] border-collapse text-[13px]">
            <caption className="sr-only">Entries in {lx.name}</caption>
            <thead>
              <tr className="text-[11px] text-muted uppercase tracking-wider text-left">
                <th scope="col" className="font-medium px-3 py-2 border-b border-line">House spelling</th>
                <th scope="col" className="font-medium px-3 py-2 border-b border-line">Variants flagged</th>
                <th scope="col" className="font-medium px-3 py-2 border-b border-line">In confirmed segments</th>
                <th scope="col" className="px-3 py-2 border-b border-line w-10"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {lx.draft.map((e) => (
                <tr key={e.word}>
                  <td className="px-3 py-2.5 border-b border-track num font-medium">{e.word}</td>
                  <td className="px-3 py-2.5 border-b border-track">
                    <span className="inline-flex flex-wrap gap-1">
                      {e.variants.length === 0 && <span className="text-xs text-subtle">none</span>}
                      {e.variants.map((v) => (
                        <span key={v} className="num inline-flex items-center bg-track rounded px-1.5 py-px text-xs text-slate-300 underline decoration-wavy decoration-warn underline-offset-[3px]">{v}</span>
                      ))}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 border-b border-track text-xs">
                    <span className="inline-flex flex-wrap items-center gap-x-2.5 gap-y-1">
                      <span className="num text-muted">
                        {num(e.house)} house · <span className={e.variant ? "text-warn" : undefined}>{plural(e.variant, "variant")}</span>
                      </span>
                      {e.review && (
                        <Link to={`/label/annotate/${e.review.dataset_id}?file=${e.review.file_id}&segment=${e.review.segment_id}`} className="text-xs">
                          Review {num(e.variant)}
                        </Link>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 border-b border-track">
                    <Button size="sm" variant="ghost" aria-label={`Remove ${e.word}`} disabled={remove.isPending} onClick={() => remove.mutate(e.word)}>
                      <I.X size={13} />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 pt-3 border-t border-track">
        <span className="text-xs text-muted">
          {lx.dirty
            ? `Unpublished changes. Annotate keeps using ${lx.version ? `v${lx.version}` : "no version of it"} until you publish ${next}.`
            : lx.version
              ? `Annotate uses v${lx.version}.`
              : "Add entries, then publish to use them in Annotate."}
        </span>
        <span className="ml-auto flex gap-2">
          {lx.dirty && lx.version !== null && (
            <Button size="sm" variant="ghost" disabled={discard.isPending} onClick={() => discard.mutate()}>Discard changes</Button>
          )}
          <Button variant="primary" disabled={!lx.dirty || lx.problems.length > 0 || publish.isPending} onClick={() => publish.mutate()}>
            Publish {next}
          </Button>
        </span>
      </div>
    </Panel>
  );
}
