// Thin fetch wrapper + the response shapes the pages use. The backend is the source of truth for
// field names (backend/concord_label/api/*.py).

import type { LexEntry } from "./lexicon";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body instanceof FormData || body instanceof Blob) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const r = await fetch(`/api${path}`, { method, headers, body: payload, ...init });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------- shapes

/** GET /api/config (CONTRACTS.md §1). `peer` is Concord Bench (BENCH_URL), and whether it answered. */
export interface AppConfig {
  app: "concord-label";
  version: string;
  contract_version: number;
  peer: { url: string | null; reachable: boolean; contract_version: number | null };
  default_normaliser: string;
  default_collar: number;
  collars: number[];
  replay: boolean;
  /** The model catalog: registering, verifying and serving models happen there (`ui_url`). */
  catalog: { url: string; ui_url: string };
  public_url: string;
}

/** A job's lease waiting in the catalog's queue ("waiting for compute"). */
export interface JobWaiting {
  models: string[];
  position: number | null;
  waiting_for: string | null;
  lease_state: string | null;
}

export interface Job {
  id: number;
  type: string;
  status: "queued" | "running" | "waiting" | "done" | "failed" | "blocked";
  progress: number;
  message: string | null;
  error: string | null;
  /** Catalog models the job has work for (it leases them one stage at a time). */
  required_services: string[];
  waiting: JobWaiting | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  label?: string;
  log?: string[];
}

export interface Dataset {
  id: number;
  uid: string;
  name: string;
  language: string | null;
  description: string | null;
  tags: string[];
  label_types: string[];
  mode: string;
  status: string;
  created_at: string;
  files: number | null;
  usable_files: number | null;
  hours: number | null;
  usable_hours: number | null;
  has_speaker_text: boolean;
  flagged: Record<string, number>;
  report?: DatasetReport;
  runs?: number;
  ingest_job?: Job | null;
}

export interface TaskMatch {
  records: number;
  files: number;
  formats: string[];
  preview: { file: string; start: number | null; end: number | null; speaker: string | null; text: string | null }[];
}

export interface Inspection {
  audio_files: number;
  audio_bytes: number;
  annotation_files: number;
  ignored: string[];
  tasks: Record<string, TaskMatch>;
  unmatched: string[];
  audio_without_labels: string[];
  duplicate_stems: string[];
  parse_errors: { file: string; format: string; errors: string[]; count: number }[];
  files_by_format: { file: string; format: string; records: number; tasks: string[] }[];
}

export interface DatasetReport {
  files: number;
  usable_files: number;
  hours: number;
  usable_hours: number;
  sample_rates: Record<string, number>;
  flagged: Record<string, { file: string; error?: string; clipped_pct?: number; duration?: number }[]>;
  tasks: Record<string, TaskMatch>;
  unmatched: string[];
  audio_without_labels: string[];
  audio_without_labels_count: number;
  duplicate_stems: string[];
  parse_errors: Inspection["parse_errors"];
  has_speaker_text: boolean;
}

/** A catalog model (GET /api/models). `id` is Label's own model id, null until a run first uses it; pickers
 *  and run requests use `catalog_id`. Capabilities come from the catalog's verifier. */
export interface Model {
  id: number | null;
  catalog_id: string;
  display_name: string;
  tags: string[];
  description: string | null;
  contract: string | null;
  api: string | null;
  engine: string | null;
  image: string | null;
  state: "running" | "stopped" | "starting" | "stopping" | "queued" | "failed" | "unknown";
  state_detail: string | null;
  leased_by: string[];
  build_key: string | null;
  capabilities: string[];
  timing_level: string | null;
  capabilities_status: string | null;
  usable_as: string | null;
  not_usable: string | null;
  stages: string[];
  /** Models page groups, from every task the catalog's verifier found (also ones Label does not run). */
  groups: ("asr" | "seg" | "enhance")[];
  cold_start_s: number | null;
  cold_start_measured: boolean;
  gpu: { fraction?: number; gb?: number } | null;
  runs: number;
  catalog_url: string;
}

export interface ModelList {
  models: Model[];
  catalog: { url: string; ui_url: string; reachable: boolean; error: string | null };
}

export interface Normaliser {
  id: string;
  key: string;
  label: string;
  description: string;
  default: boolean;
  service: string | null;
}

/** A label run (pseudo-labelling a dataset); the models it used are in `params`. */
export interface Run {
  id: number;
  task: string;
  label: string;
  dataset_id: number;
  dataset: string;
  status: string;
  error: string | null;
  /** Per model, the build it ran with. */
  image_tags: Record<string, { image?: string | null; build_key?: string | null }>;
  params: Record<string, unknown>;
  rtf: Record<string, number | null>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ErrorRegion {
  type: "missed" | "false_alarm" | "confusion";
  start: number;
  end: number;
  dur: number;
}

export interface Peaks {
  per_second: number;
  duration: number;
  min: number[];
  max: number[];
}

// ---------------------------------------------------------------- Label (Phase 2)

export type SegStatus = "pseudo" | "auto" | "confirmed" | "rejected";
export type BarKey = "confirmed" | "auto" | "review" | "rejected" | "running" | "unlabelled";

export interface LabelProgress {
  bar: Record<BarKey, number>;
  seconds: number;
  segments: Record<"confirmed" | "auto" | "review" | "rejected", number>;
  files: { labelled: number; unlabelled: number };
  sources: string[];
  tasks: string[];
  flagged: number;
  running: boolean;
}

export interface NextAction {
  kind: "running" | "pseudo" | "review" | "live";
  label: string;
  status?: SegStatus;
}

export interface ConsoleRow {
  id: number;
  name: string;
  tags: string[];
  language: string | null;
  mode: string;
  label_types: string[];
  label_source: string;
  tasks: string[];
  hours: number | null;
  progress: LabelProgress;
  next: NextAction;
  label_run: { id: number; status: string } | null;
  created_at: string;
}

/** saved: every output here; bench: here or saved in Concord Bench (found through the catalog's index). */
export type CacheState = "saved" | "bench" | "partial" | "new";

interface PreviewModel {
  model_id: number;
  name: string;
  /** The catalog model id. */
  service: string;
  state: CacheState;
  model_state: string | null;
  cold_start_s: number | null;
  cold_start_measured: boolean | null;
}

export interface PreviewMember extends PreviewModel {
  timing_level: string | null;
  cached_cuts: number;
  bench_cuts: number;
  known_cuts: number;
  files_without_segments: number;
}

export interface PlanStep {
  stage: string;
  model: string;
  load: boolean;
  cold_start_s: number;
  items: number | null;
  hits: number | null;
  missing: number | null;
  skip: boolean;
}

export interface LabelPreview {
  files: number;
  seg: PreviewModel & { cached_files: number; bench_files: number };
  members: PreviewMember[];
  saved_members: number;
  bench_members: number;
  new_members: number;
  /** The catalog's plan (stage by stage); null when the catalog did not answer. */
  plan: { loads: number; est_load_s: number; steps: PlanStep[] } | null;
}

export interface Segment {
  id: number;
  uid: string;
  file_id: number;
  start: number;
  end: number;
  speaker: string | null;
  text: string | null;
  status: SegStatus;
  confidence: number | null;
  source_run_id: number | null;
  flagged: boolean;
  flag_note: string | null;
  reviewed: boolean;
  edited_at: string | null;
  updated_at: string | null;
  chosen: string | null;
  agreement: Record<string, number> | null;
  words: { w: string; start: number; end: number }[] | null;
  /** Drawn by hand in Annotate rather than from a model or the references. */
  created: boolean;
  /** Spelling hints the annotator chose to keep (lower case). */
  spelling_kept: string[];
  alternatives?: { service: string; model: string; text: string; build_key: string | null; source_app: string | null }[];
  file?: string;
}

export interface LabelSummary {
  segments: number;
  files: number;
  triage: Partial<Record<SegStatus, number>>;
  kept_reviewed: number;
  replaced: number;
  agreement: { members: string[]; cells: { ref: string; hyp: string; wer: number | null; n: number }[] };
  chosen: { member: string; n: number; share: number | null }[];
  strategy_applied: Record<string, number>;
  thresholds: { high: number; low: number };
}

export interface LabelResults {
  dataset: Dataset;
  run: Run | null;
  job: Job | null;
  summary: LabelSummary | null;
  model_names: Record<string, string>;
  segmenter: string | null;
  progress: LabelProgress;
  next: NextAction;
  queue: Segment[];
}

export interface LabelFile {
  id: number;
  path: string;
  stem: string;
  duration: number | null;
  status: string;
  usable: boolean;
  counts: { confirmed: number; auto: number; review: number; rejected: number; flagged: number };
}

export interface AnnotateView {
  file: { id: number; path: string; stem: string; duration: number; status: string; dataset_id: number; meta: Record<string, unknown> };
  dataset: { id: number; name: string; language: string | null };
  segments: Segment[];
  speakers: string[];
  /** The segmentation model of the label run these segments came from; null without one. */
  diariser: string | null;
  keywords: unknown[];
  thresholds: { high: number; low: number };
  prev_file_id: number | null;
  next_file_id: number | null;
  position: { index: number; of: number };
}

export interface LiveModel {
  /** null: a catalog model Label never used, listed for its locked score in Bench alone. */
  model_id: number | null;
  name: string;
  service: string;
  coverage: number;
  words: number;
  wer: number | null;
  ci: [number, number] | null;
  curve: { confirmed: number; wer: number | null }[];
  /** The model's locked benchmark score in Concord Bench (newest version of this dataset there); null without Bench. */
  locked: { wer: number | null; run_id: number; normaliser: string; dataset: string | null; export_version: number | null } | null;
  rank: number | null;
}

export interface LiveEval {
  dataset: { id: number; name: string };
  asr: { normaliser: string; confirmed: number; min_rank: number; ranked: boolean; models: LiveModel[] };
  der: { files: number; of: number; collar: number; models: { model_id: number; name: string; der: number | null; jer: number | null; files: number }[] };
  kws: { available: boolean };
  feed: { id: number; file_id: number; file: string; start: number; end: number; speaker: string | null; text: string | null; confirmed_at: string }[];
}

export interface SyncPackage {
  id: number;
  direction: "export" | "import";
  status: "built" | "staged" | "applied" | "undone";
  dataset_id: number | null;
  dataset_name: string | null;
  scope: Record<string, unknown>;
  counts: Record<string, unknown> & { files?: number; segments?: number; by_status?: Record<string, number>; outcome?: Record<string, number> };
  include: { pseudo_labels: boolean; model_outputs: boolean; audio: boolean } | null;
  source_install: string | null;
  created_at: string;
  applied_at: string | null;
  undone_at: string | null;
  conflicts_open: number;
  conflicts: number;
  file: string | null;
}

export interface PkgSegment {
  uid?: string;
  file_sha256: string;
  start: number;
  end: number;
  speaker: string | null;
  text: string | null;
  status: string;
  confidence: number | null;
  source: string;
  reviewed?: boolean;
  updated_at: string | null;
}

export interface SegmentConflict {
  kind?: undefined;
  key: string;
  file_sha256: string;
  local: PkgSegment & { id: number };
  incoming: PkgSegment;
  resolution: "incoming" | "local" | null;
}

/** One lexicon as published here or in a label package. */
export interface LexiconSide {
  version: number | null;
  entries: LexEntry[];
}

/** The same lexicon at the same version with different entries on both sides. */
export interface LexiconConflict {
  kind: "lexicon";
  key: string;
  uid: string;
  name: string;
  local: LexiconSide & { id: number };
  incoming: LexiconSide;
  resolution: "incoming" | "local" | null;
}

export type Conflict = SegmentConflict | LexiconConflict;

export interface LexiconPlanRow {
  key: string;
  uid: string;
  name: string;
  outcome: "add" | "unchanged" | "keep_local" | "take_incoming" | "conflict";
  incoming: LexiconSide;
  local: (LexiconSide & { id: number }) | null;
}

export interface ImportStaged extends SyncPackage {
  manifest: Record<string, unknown> & { dataset?: { name: string; language: string | null; tags: string[] }; created_at?: string; install_id?: string };
  suggestions?: { dataset_id: number; name: string; matched_files: number; package_files: number; same_name: boolean }[];
  conflict_list?: Conflict[];
  rules?: { outcome: string; rule: string }[];
}

export interface ImportPlan {
  target: { id: number; name: string } | null;
  files: { path: string; sha256: string; duration: number | null; sample_rate: number | null; outcome: string }[];
  file_counts: Record<string, number>;
  counts: Record<string, number>;
  rows: { outcome: string; reason?: string; incoming: PkgSegment; local?: PkgSegment & { id: number } }[];
  truncated: boolean;
  conflicts: Conflict[];
  lexicons: LexiconPlanRow[];
  rules: { outcome: string; rule: string }[];
}

// ---------------------------------------------------------------- Lexicons

export interface LexiconSummary {
  id: number;
  uid: string;
  name: string;
  description: string | null;
  /** Latest published version; null before the first publish. */
  version: number | null;
  published_at: string | null;
  /** Entries in the draft. */
  entries: number;
  /** The draft differs from the published version. */
  dirty: boolean;
  /** Datasets whose confirmed segments use one of its words. */
  datasets: number;
}

export interface LexiconEntryRow extends LexEntry {
  /** Occurrences in confirmed segments, of the house spelling and of the variants. */
  house: number;
  variant: number;
  /** The first confirmed segment holding a variant. */
  review: { dataset_id: number; file_id: number; segment_id: number } | null;
}

export interface LexiconDetail extends LexiconSummary {
  draft: LexiconEntryRow[];
  /** Clashes with itself or another lexicon that stop it being published. */
  problems: string[];
  versions: { version: number; entries: number; published_at: string; imported_from: number | null }[];
}

// ---------------------------------------------------------------- Send to Bench (CONTRACTS.md §2, §4)

export type Peer = AppConfig["peer"];

export interface ReferencePreview {
  scope: "complete" | "any";
  files: number;
  segments: number;
  hours: number;
  left_out: number;
  label_types: string[];
  refs_file: string | null;
  problem: string | null;
  review: Record<string, number>;
  next_version: number;
  models: string[];
  /** File labels sent along for Bench to slice by (refs/attributes.json, CONTRACTS.md §2.7). */
  labels: string[];
  dataset: { id: number; name: string; uid: string; tags: string[] };
  peer: Peer;
  destination: "push" | "download";
  push_refused: string | null;
}

export interface ReferenceExport {
  id: number;
  dataset_id: number;
  version: number | null;
  name: string;
  scope: "complete" | "any";
  tags: string[];
  include_audio: "auto" | "always" | "never";
  include_outputs: boolean;
  destination: "push" | "download";
  status: "queued" | "running" | "done" | "failed";
  counts: { files?: number; segments?: number; hours?: number; audio_files?: number; outputs?: number; left_out?: number; labelled_files?: number; bytes?: number };
  label_types: string[] | null;
  audio_included: "all" | "partial" | "none" | null;
  file: string | null;
  download: string | null;
  bench_url: string | null;
  bench_import_id: number | null;
  bench_dataset_id: number | null;
  bench_preview: Record<string, unknown>;
  error: string | null;
  job_id: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface FlagsImport {
  matched: number;
  unmatched: number;
  dataset_id: number;
  dataset: string;
}
