# Concord Label

Pseudo-label speech audio with ensembles of models served by the personal AI platform, review the labels
by hand, and turn them into references. Labels cover transcripts (ASR) and speaker turns (diarisation).

Concord Label is one of two apps that came from Concord (SPLIT_PLAN.md):

- **Concord Bench** benchmarks models on reference datasets and analyses their errors (port 8600).
- **Concord Label** (this repository) produces those references (port 8620).

The two apps share no code. They agree only on the formats and endpoints in [CONTRACTS.md](CONTRACTS.md),
which is kept identical in both repositories (`just check-contracts`). Each app has its own data directory
and database. Finished references go to Bench with "Send to Bench" (a reference package, pushed when
`BENCH_URL` answers, else downloaded); Bench links back to files in Label and sends flags back. Every link
is optional: without Bench, Label works on its own.

Concord Label does not register, verify, start or call models itself. The platform's **model catalog**
(`../../platform/catalog/README.md`) does: Label lists the catalog's models, takes a lease from it for each
stage of a job, sends every inference call through its gateway (via `aicore`), and uses its output index so a
model already run on a file, here or in Concord Bench, is not run again.

## Quickstart

```bash
# prerequisites: uv, just, Node 22+, ffmpeg; the platform repo at ../../platform (rendered), and its
# model catalog running for real inference (`just catalog-up` there; http://127.0.0.1:8700)
just setup            # uv sync + npm install
just dev              # API :8620 + worker + Vite :5174 -> open http://localhost:5174
just test             # backend + frontend unit tests, no GPU, platform or catalog needed
just lint             # ruff, import-linter (concord_label must not import concord_improve), tsc
just e2e              # Playwright on replayed fixtures (Docker; needs the concord-label image, `just build`)
just e2e-handoff      # the round trip with Concord Bench: both images on one Docker network (see Two apps)
just check-contracts  # check contracts/samples and compare with Concord Bench next to this repository
```

`CONCORD_PORT` and `CONCORD_WEB_PORT` change the ports. `just api` serves the built frontend
(`just build-web`) from the API port, without Vite.

Without a GPU, `REPLAY_DIR=fixtures/recorded AICORE_ENDPOINTS=fixtures/recorded/endpoints.generated.yaml just dev`
serves recorded model responses for the fixture set, so the whole UI works offline. In replay mode no catalog
is needed either: an in-memory stand-in built from the recordings lists the fixture models as running.

To try it on the fixture set: register `pyannote-diarization`, `whisper-large-v3` and
`faster-whisper-large-v3` in the model catalog's UI (in replay mode they are there already; Models shows
them); Datasets → Upload dataset → Audio only → Local path → `<repo>/fixtures/generated/dataset/audio`; then
Pseudo-label that dataset and review it in Annotate.

The database starts empty (SPLIT_PLAN.md §8). Nothing is migrated from the single-app Concord. Labelling
work in progress moves over as a label package (Sync).

## Two apps

Concord Label (this repository) and Concord Bench (`../Concord`) run side by side, each with its own data
directory and database.

- **Development.** `just dev` here (API 8620, Vite 5174) and `just dev` in Bench (API 8600, Vite 5173).
  Each finds the other through one setting in its `.env`: `BENCH_URL=http://localhost:8600` here and
  `LABEL_URL=http://localhost:8620` in Bench. Unset, the links between them are hidden and packages are
  downloaded instead of pushed.
- **Handoff.** **Send to Bench** on a dataset (dataset page or live evaluation) writes a reference package
  of its confirmed segments (CONTRACTS.md §2): pushed to Bench when it answers (only the audio Bench lacks
  travels), or downloaded as a zip. In Bench, **Import reference package** stages it with a preview;
  accepting it makes a new dataset there, and a later package of the same dataset becomes version N. Back
  the other way: Bench's "Open in Label" opens the file here, the live evaluation shows Bench's locked
  score for each model, and Bench's "Send flags to Label" flags segments here.
- **Round trip.** `just e2e-handoff` runs both images (`just build` in each repository) on one Docker
  network in replay mode and drives them through their APIs (`tests/test_handoff.py`, marker `handoff`,
  not in `just test`): the fixture set benchmarked in Bench directly, the same set uploaded here and sent
  by push (no audio travels) and by download, versions 1 and 2 accepted, the WER equal to the direct
  benchmark every time, the locked score shown here and a flag sent back. Everything is removed afterwards.
- **Offline.** A full offline kit written by either app can hold Label, Bench or both; with both, they
  start from one compose file and already know each other's address (see Offline kits).
- **Contracts.** `just check-contracts` checks `contracts/samples` and compares `CONTRACTS.md` and
  `contracts/` with the Bench repository (`--peer PATH`; by default `../Concord` next to this one); they
  must stay byte-identical.
- **Fixtures.** Bench owns `fixtures/generated` and `fixtures/recorded`. When Bench regenerates them
  (`just fixtures`, `just record`), copy both folders into this repository.

## Layout

```
backend/concord_label/
  core/        settings, paths (the only module that knows where files live), SQLite models, db
  datasets/    audio (ffmpeg probe/decode, clipping, 16 kHz derive, peaks), annotation parsers, ingest
  platform/    catalog.py: the model catalog (models, leases, replay stand-in, app registration);
               tools.py: running the platform's tools for offline kits
  adapters/    openai_asr, openai_chat_audio, oip_segmenter; record / replay transport
  runs/        job queue + worker (ingest, label, export, reference), output cache + the catalog's index
  cuts.py      a dataset's usable files; segmenter output -> merged, padded ASR cuts
  metrics/     normalisers, wer (jiwer), der/jer (pyannote.metrics), vad
  label/       ensemble fusion, pseudo-label runs, console, annotate edits, live evaluation,
               label packages (sync), reference packages for Bench (refpkg), flags from Bench (flags)
  peer.py      Concord Bench: /api/config probe (cached), locked scores
  offline/     offline kits
  api/         FastAPI routers
  migrations/  Alembic (0001 initial schema, 0002 dataset uid + reference_export, 0003 no duration cap,
               0004 models from the catalog, outputs keyed on build key)
backend/concord_improve/   Phase 3 placeholder router; on hold, installed in no image
frontend/src/  React + TanStack Query + Tailwind (AeroAudio tokens in index.css)
contracts/     the contract samples and their checker (CONTRACTS.md); identical in Concord Bench
fixtures/      generated/ (audio, refs, templates, check clip) and recorded/ (model responses for replay),
               both copied from Concord Bench
tests/         pytest; `-m live` for tests against running services, `-m handoff` for the round trip with Bench
```

### Code copied from Concord Bench

Both apps started from the same code, and each keeps its own copy (SPLIT_PLAN.md §2.2, §11). A fix in
one of these modules is checked against Concord Bench in the same sitting:
`core/`, `datasets/`, `platform/`, `adapters/`, `runs/cache.py`, `runs/jobs.py`,
`runs/worker.py`, `cuts.py` (`prepare_cuts`, `usable_files`), `metrics/` (`wer`, `der`, `normalisers`,
`vad`), `offline/`; in the frontend `components/` (`AudioPlayer`, `Timeline`, `timelineMath`, `TagInput`,
`icons`, `ui`), `lib/` and the Models, Jobs, Upload and Offline export pages. The adapters' params (and so
`params_hash`, via `aicore.catalog.params_hash`) must stay identical in both apps: the catalog's output
index matches outputs across them on build key + audio + span + params hash. Before the model catalog,
`registry/` (registry reader, compatibility check) and the Add model and Contract pages were copies too;
both apps dropped them for the catalog's own UI.

## How it works

- **Datasets.** Upload a folder or zip (chunked), or point at a local folder: audio alone, or audio with
  complete or partial annotations. Originals are copied to `CONCORD_DATA/originals/<sha256>`; models and
  playback use a derived 16 kHz mono FLAC. There is no length limit, but each file (or cut) goes to a model
  in one request, so a very long file works only if the model takes it: VAD and diarisation receive the
  audio as JSON numbers, about 1.25 GB per hour of audio.
  Annotations are matched to audio by file stem and become confirmed segments: ASR (JSONL / NeMo
  manifest / CSV / `.txt`), diarisation (RTTM / JSON segments / NeMo diarisation manifest plus its RTTMs),
  speaker-attributed transcripts (JSON/JSONL segments, STM, SegLST, NeMo diarisation output JSON).
  Templates are under `fixtures/generated/templates`.
- **Models** come from the model catalog: it registers them (pick a platform service, add tags), verifies
  them on its check clip and records capabilities (ASR / VAD / diarisation), timing (word / segment / text),
  cold-start time and the **build key** (image + config + weights) of the current build. The Models page
  lists them read-only (state, capabilities, cold start, build) and links to the catalog's UI
  (`CATALOG_UI_URL`) for registering. Label keeps a `model` row per catalog model it has used (runs and
  outputs refer to model ids), created on first use and refreshed from the catalog, so live evaluation and
  exports still work while the catalog is down.
- **Jobs.** One worker process works through the `job` table: ingest, label run, offline kit, Send to Bench.
  Among queued jobs it takes the oldest whose models are all running in the catalog, else the oldest. A
  label run leases its models from the catalog itself, one stage at a time, as `label:job-<id>` (aicore's
  client heartbeats every 30 s; a crashed worker's lease expires after 90 s). While the catalog queues the
  lease the job is **waiting**, with its queue position and what it waits for ("waiting for compute #2" in
  the nav and on the Jobs page). If a model fails to start, or the catalog does not answer, the job is
  `blocked` with the catalog's message; Retry runs it again and skips what is already saved.
- **Outputs and the index.** Every model call goes through the output cache (`runs/cache.py`), keyed on model,
  audio, span, params hash and build key (the params never hold the image or build). Lookups are batched per
  file or stage: Label's own rows, then the catalog's output index; an output Concord Bench already made is
  read from Bench (`POST /api/outputs/batch`) and copied into Label's `model_output` with its provenance
  (`source_app`, `source_id`), then recorded in the index as Label's copy. Everything Label computes is
  recorded in the index too, and Label serves `POST /api/outputs/batch` for Bench; the API registers
  `PUBLIC_URL` with the catalog at startup (retried in the background while the catalog is down). A new
  build means new outputs: rows from an older build (and rows from before the catalog, which have no build
  key) stay readable by id but never match. Outputs of a model whose build key the catalog does not know yet
  are kept but never indexed or matched.
- **Console** (`/label`): label progress per dataset in audio time (confirmed / auto-accepted / needs
  review / rejected / running / not labelled) and the next action.
- **Pseudo-label**: segments come from one VAD / diarisation model; close or short same-speaker segments
  are merged and each cut is padded by 0.1 s (`cuts.prepare_cuts`, the same cuts Concord Bench's
  pipelines use); every ASR member transcribes each cut. A run goes **stage by stage**, so each model
  loads once and only one is leased at a time: the segmenter over every file (one lease), then the cuts,
  then each ASR member over every cut (one lease per member, released before the next, in the order of the
  catalog's plan), then fusion with no model. Outputs saved here or in Bench are reused and a stage with
  nothing missing takes no lease, so re-fusing needs no model at all; outputs are committed per file, so a
  retried run skips finished work. The set-up panel shows each model's state, capabilities and cold start,
  per model whether its outputs are saved, "saved in Bench" (one index lookup per model), partly saved or
  new, and the catalog's plan (loads and estimated cold-start time). Fusion: best-aligned hypothesis (default), ROVER over word-timed
  members, or one model. Confidence = member agreement; triage >= 0.85 auto-accept, 0.50–0.84 review,
  below rejected. Pseudo-labels go into `segment` with the label run as `source_run_id`.
- **Annotate**: whole-file timeline (one lane per speaker, overlap hatch, flags lane), segment grid with
  shift-click multi-select and a bulk bar, inline transcript editing with autosave, speaker pill cycling,
  merge / split / add. Shortcuts: Space, Tab, 1–8, V, M, Enter. Any edit marks the segment as touched by
  a person; pseudo-labelling never replaces it afterwards.
- **Live evaluation**: WER of each model against confirmed segments (from saved outputs only), "too few
  to rank" below 30, bootstrap 95% intervals, DER on fully reviewed files, the latest confirmations.
  Metrics: WER via jiwer after a normaliser (`basic` default, `whisper_en`, `none`, or any platform
  `text.transform` service followed by `basic`); DER/JER via pyannote.metrics with a collar (default
  0.25 s each side). Only outputs of each model's current build count. With Concord Bench reachable, each model's locked score there (newest version of
  this dataset, CONTRACTS.md §4) shows next to its live WER; otherwise the column is hidden.
- **Send to Bench** (live evaluation and dataset pages; replaces the single-app Promote): a reference
  package (CONTRACTS.md §2) of confirmed segments only. Scope `complete` takes fully reviewed files (ASR +
  DER), `any` every file with a confirmed segment (ASR only). The references file follows what the
  segments carry (SegLST, JSONL or RTTM), with segment uids so flags can come back; audio auto / always /
  never; optionally the segmenter's whole-file outputs with their full params, image and catalog build key
  (Bench matches on the build key). With `BENCH_URL`
  reachable on the same contract version the package is pushed (Bench is asked which audio it lacks, the
  zip goes up in 8 MB chunks, Bench stages it as a reference import to accept there); otherwise it is a
  zip to download. Every send is a new `export_version`, listed on the dataset page.
- **Links from Bench**: `/label/file/by-sha/<sha256>?dataset_uid=<uid>&t=<s>` opens the annotator on that
  file at `t`; `POST /api/flags/import` (or a flags file uploaded on the dataset page) flags segments,
  matched by uid or by overlap, and they show in the annotator's flags lane.
- **Sync**: label packages between Label installs (`manifest.json`, `files.csv`, `segments.jsonl`,
  `keywords.jsonl`, optional `runs/` and `audio/`). Import is staged and previewed; files match by
  SHA-256, segments by `uid` or time overlap. Reviewed work is never overwritten by pseudo-labels,
  reviewed-on-both-sides differences become conflicts to resolve, and a snapshot taken before the merge
  makes it undoable.
- **`GET /api/config`** reports `app` (`concord-label`), `version`, `contract_version` and `peer`
  (CONTRACTS.md §1): `BENCH_URL`, whether Bench answered `/api/config` (1 s timeout, cached 30 s) and its
  contract version.

## Settings

Environment variables (or `.env`, see [.env.example](.env.example)): `CONCORD_DATA` (default `data/` in
this repository), `CONCORD_PORT` (8620), `CONCORD_WEB_PORT` (5174), `PLATFORM_DIR`, `AICORE_ENDPOINTS`,
`AI_HOST`, `REPLAY_DIR`, `RECORD_DIR`, `CONCORD_HOME`, `CONCORD_IMAGE`, `BENCH_URL` (Concord
Bench, unset by default; e.g. `http://localhost:8600`, or `http://concord-bench:8600` on a shared Docker
network), and for the model catalog:

- `CATALOG_URL` or `AICORE_CATALOG` (default `http://127.0.0.1:8700`; `http://catalog:8700` in the image).
  Label exports it as `AICORE_CATALOG`, so `aicore.client(name)` goes through the catalog's gateway.
- `CATALOG_UI_URL`: where the Models page links for registering models (default: the catalog URL).
- `PUBLIC_URL`: where the catalog and Bench reach this API for `POST /api/outputs/batch` (default
  `http://127.0.0.1:<CONCORD_PORT>`; `http://concord-label:8620` in the image).

`NO_EVICT` and the service start timeout are gone: the catalog's scheduler decides what runs.

Label's schema versions start with `label_`, and Label refuses to start (the API, the worker and
`just migrate`) on a database it did not write — Concord Bench's, or one from the single Concord app
before the split — without changing it. Point `CONCORD_DATA` at an empty folder to start fresh.

## Fixtures and recordings

Concord Bench owns the fixture sources. It regenerates `fixtures/generated` (`just fixtures`) and
re-records `fixtures/recorded` against live services (`just record`). When either changes there, copy the
folder into this repository. Label's tests and the e2e server use both: the replay catalog lists the fixture
models with the capabilities recorded in `fixtures/recorded/summary.json`, and they pseudo-label the fixture
audio on recorded responses. Recordings are named by the adapter params' hash; the params lost `image` with
the model catalog, and `fixtures/rekey_recordings.py` renamed the existing recordings instead of recording
them again (run it on a copy from Bench if Bench's recordings still carry `image`).

## Image

`just build` builds `concord-label:0.2.0` (API and worker on port 8620). Improve is not in it. To run it
next to the platform:

```bash
docker run -d --name concord-label --network ai-net -p 127.0.0.1:8620:8620 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(realpath ../../platform)":/platform -v concord-label-data:/data concord-label:0.2.0
```

The image sets `CATALOG_URL=http://catalog:8700` and `PUBLIC_URL=http://concord-label:8620`, so on `ai-net`
it finds the catalog (`just catalog-up` in the platform repo) and the catalog finds it. Every inference call
goes through the catalog's gateway. The Docker socket and the platform mount are only for offline kits
(the platform's `tools/bundle.py`); mount the platform at the same absolute path it has on the host if its
compose file uses relative bind mounts. In development, run Concord Label on the host instead.

## Offline kits

Concord Label, Concord Bench or both, and chosen models, can go to a machine with no internet access
(CONTRACTS.md §6). **Offline export** lists every
platform service with its image and weight sizes; tick the models, pick a kit kind, tick the apps a full
kit carries ("Include Concord Label" is ticked; "Include Concord Bench" shows Bench's local image tag, or
says to run `just build` in that repository) and a destination folder, and the worker writes the kit there
(progress on the Jobs page). From the command line:

```bash
just export-offline silero-vad whisper-large-v3 --kind full --out /mnt/usb   # Concord Label + platform + models
just export-offline silero-vad --kind full --app concord-label --app concord-bench --out /mnt/usb  # both apps
just export-offline whisper-large-v3-turbo --kind models --out /mnt/usb       # models only, for an existing install
just verify-kit /mnt/usb/concord-offline-...                                  # checksums against manifest.json
```

`--app concord-bench=concord-bench:0.2.0` picks a tag; without one the other app's image is its contract
name, `concord-bench:<this version>`, or else the first tag `docker images concord-bench` lists.

- A full kit carries `images/concord-label.tar` and/or `images/concord-bench.tar` (listed as `"apps"` in
  `manifest.json`). `./install.sh` loads them, writes `concord/.env` (`LABEL_IMAGE`, `BENCH_IMAGE`) and
  starts the apps from `concord/compose.yml`: `concord-label` on http://127.0.0.1:8620 and `concord-bench`
  on http://127.0.0.1:8600. With both, `BENCH_URL` and `LABEL_URL` point at each other, so Send to Bench
  pushes and the locked score shows on the offline machine too.
- `./install.sh --add <install>` merges a model kit into an install. Register models there in the model
  catalog: no registrations or datasets travel; labels come back as label packages.
- The air-gap test of a kit (`just test-offline`, with Bench alone and with both images) lives in Concord
  Bench.

## Status

Milestones S2, S4, S5, S6 and S7 of SPLIT_PLAN.md (Label side): Label runs on its own, sends references to
Bench, links with it, writes offline kits with either or both apps, and the round trip with the
`concord-bench` image passes (`just e2e-handoff`, SPLIT_PLAN.md §10). The split is built. Since then, models,
leases and inference moved to the platform's model catalog (MODEL_CATALOG_REDESIGN.md in the platform repo).

Known limits:
- KWS is not built (Q6). Only its hooks exist: the enums, the `keyword_event` table, the disabled
  Keywords focus in Annotate and an empty `keywords.jsonl` in label packages.
- Label packages do not carry deletions: a segment deleted on one side comes back if the other side
  still has it.
- Improve (Phase 3) is on hold; `backend/concord_improve` is a placeholder installed in no image.
- The fixture set is synthesised speech (kokoro-tts): good for correctness, not realism.
- The worker runs one job at a time, so while a label run waits in the catalog's queue, later jobs (an
  ingest, say) wait behind it.
