import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Job } from "../../lib/api";
import { ago, etaText, jobStatusText, stageText } from "../../lib/format";
import { Button, cx, Empty, ErrorNote, JobPill, PageHeader, Panel, Spinner } from "../../components/ui";

export function JobsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api.get<Job[]>("/jobs?limit=100"), refetchInterval: 2000 });
  const [open, setOpen] = useState<number | null>(null);
  const log = useQuery({ queryKey: ["job", open], queryFn: () => api.get<Job>(`/jobs/${open}`), enabled: open !== null, refetchInterval: 1500 });
  const retry = useMutation({ mutationFn: (id: number) => api.post(`/jobs/${id}/retry`), onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }) });
  const cancel = useMutation({
    mutationFn: (id: number) => api.post(`/jobs/${id}/cancel`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["job"] });
    },
  });
  const sel = log.data;
  const canCancel = !!sel?.cancellable && ["queued", "running", "waiting"].includes(sel.status) && !sel.cancelling;
  return (
    <>
      <PageHeader crumbs="Concord Label" title="Jobs" subtitle="Ingests, pseudo-label runs, publishes to the Dataset Catalog and sends to Bench. One worker runs them in order; a pseudo-label run leases its models from the Model Catalog one stage at a time, and waits for compute when the catalog queues it." />
      <div className="flex flex-col xl:flex-row gap-5 items-start">
        <Panel className="flex-1 w-full">
          {q.isLoading && <Spinner />}
          <ErrorNote error={q.error ?? retry.error ?? cancel.error} />
          {q.data?.length === 0 && <Empty>No jobs yet.</Empty>}
          {q.data?.map((j) => (
            <button
              key={j.id}
              type="button"
              onClick={() => setOpen(j.id)}
              className={cx("text-left flex flex-wrap items-center gap-3 bg-transparent border rounded-md px-3 py-2 cursor-pointer", open === j.id ? "border-focus" : "border-transparent hover:bg-track")}
            >
              <span className="num text-xs text-subtle w-10">#{j.id}</span>
              <JobPill status={j.status} progress={j.progress} cancelling={j.cancelling} />
              <span className="flex-1 min-w-[200px] text-[13px]">{j.label}</span>
              {j.status === "waiting" ? (
                <span className="text-xs text-warn truncate max-w-[360px]" title={j.waiting?.waiting_for ?? undefined}>{jobStatusText(j)}{j.waiting?.waiting_for ? `: ${j.waiting.waiting_for}` : ""}</span>
              ) : (
                <span className="text-xs text-muted truncate max-w-[360px]">{j.status === "done" ? "" : j.error ?? j.message ?? ""}</span>
              )}
              {etaText(j) && <span className="text-xs num">{etaText(j)}</span>}
              <span className="text-xs text-subtle">{ago(j.finished_at ?? j.started_at ?? j.created_at)}</span>
            </button>
          ))}
        </Panel>
        <Panel
          className="w-full xl:w-[520px] shrink-0"
          title={open ? `Job #${open}` : "Log"}
          actions={
            sel && ["failed", "blocked", "cancelled"].includes(sel.status) ? (
              <Button size="sm" onClick={() => retry.mutate(sel.id)}>Retry</Button>
            ) : canCancel ? (
              <Button size="sm" variant="danger" disabled={cancel.isPending} onClick={() => cancel.mutate(sel.id)}>Cancel</Button>
            ) : undefined
          }
        >
          {!open && <span className="text-[13px] text-muted">Pick a job to see its log.</span>}
          {log.data && (
            <>
              <span className="text-xs text-muted">{log.data.label} · models with work: {log.data.required_services.join(", ") || "none"}</span>
              {log.data.eta && ["running", "waiting"].includes(log.data.status) && !log.data.cancelling && (
                <span className="text-xs">
                  {stageText(log.data.eta)}
                  {etaText(log.data) && <span className="text-muted"> · {etaText(log.data)}</span>}
                </span>
              )}
              {log.data.cancelling && <span className="text-xs text-warn">Cancelling. The job stops before its next cut or stage.</span>}
              {log.data.status === "waiting" && !log.data.cancelling && (
                <span className="text-xs text-warn">
                  Waiting for compute{log.data.waiting?.position ? ` (#${log.data.waiting.position} in the catalog's queue)` : ""}: {log.data.waiting?.waiting_for ?? "the catalog is starting the models"}
                </span>
              )}
              {log.data.error && <ErrorNote error={log.data.error} />}
              <pre className="m-0 max-h-[60vh] overflow-auto scroll-thin bg-canvas border border-track rounded-md p-2.5 text-[11px] text-muted num whitespace-pre-wrap">{(log.data.log ?? []).join("\n") || "(no output yet)"}</pre>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
