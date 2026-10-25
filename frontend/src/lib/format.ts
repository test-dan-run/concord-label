// Number and time formatting. Every value rendered with these goes inside a `.num` element.

export const DASH = "—";

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  return `${(v * 100).toFixed(digits)}%`;
}

/** Signed percentage-point delta, e.g. "+1.2" / "−0.4". */
export function deltaPts(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  const x = v * 100;
  if (Math.abs(x) < 0.05) return "±0.0";
  return `${x > 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`;
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** mm:ss.mmm */
export function clock(t: number | null | undefined, ms = true): string {
  if (t === null || t === undefined || Number.isNaN(t)) return DASH;
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const ss = ms ? s.toFixed(3).padStart(6, "0") : Math.floor(s).toString().padStart(2, "0");
  return `${m.toString().padStart(2, "0")}:${ss}`;
}

export function hours(h: number | null | undefined): string {
  if (h === null || h === undefined) return DASH;
  if (h < 1) return `${(h * 60).toFixed(h * 60 < 10 ? 1 : 0)} min`;
  return `${h.toFixed(1)} h`;
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function capLabel(c: string): string {
  return { asr: "ASR", vad: "VAD", diar: "Diarisation", kws: "KWS" }[c] ?? c;
}

export function timingLabel(t: string | null | undefined): string {
  return { word: "word times", segment: "segment times", text: "text only" }[t ?? ""] ?? DASH;
}

/** A model's cold start ("~20 s", "~3 min"); "est." when the catalog has not measured it yet. */
export function coldStart(s: number | null | undefined, measured = true): string {
  if (s === null || s === undefined) return DASH;
  const t = s < 90 ? `~${Math.round(s)} s` : `~${Math.round(s / 60)} min`;
  return measured ? t : `${t} (est.)`;
}

/** A span of time in words: "less than a minute", "12 min", "1 h 5 min". */
export function duration(s: number): string {
  if (s < 60) return "less than a minute";
  const m = Math.round(s / 60);
  if (m < 90) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** How long a running job has left: "about 12 min left", or after the wait for compute. Empty without an estimate. */
export function etaText(j: { status: string; cancelling?: boolean; eta?: { remaining_s: number | null; fusing: boolean } | null }): string {
  if (!j.eta || j.cancelling || !["running", "waiting"].includes(j.status)) return "";
  if (j.eta.fusing) return "almost done";
  if (j.eta.remaining_s === null) return "estimating time left";
  if (j.status === "waiting") return `about ${duration(j.eta.remaining_s)} once it gets compute`;
  return j.eta.remaining_s < 60 ? "less than a minute left" : `about ${duration(j.eta.remaining_s)} left`;
}

/** Where a pseudo-label run is: "Stage 2 of 3: whisper-large-v3, 41 of 162 cuts". */
export function stageText(e: { stage: number; stages: number; model: string | null; unit: string | null; done: number | null; total: number | null; fusing: boolean }): string {
  if (e.fusing) return "Fusing the transcripts";
  const count = e.total ? `, ${e.done ?? 0} of ${e.total} ${e.unit}` : "";
  return `Stage ${e.stage} of ${e.stages}: ${e.model}${count}`;
}

/** A job's status in a few words: "waiting for compute #2" while its lease is queued in the catalog. */
export function jobStatusText(j: { status: string; progress: number; waiting?: { position: number | null } | null; cancelling?: boolean }): string {
  if (j.cancelling) return "cancelling";
  if (j.status === "running") return `${Math.round(j.progress * 100)}%`;
  if (j.status === "waiting") return j.waiting?.position ? `waiting for compute #${j.waiting.position}` : "waiting for compute";
  return j.status;
}
