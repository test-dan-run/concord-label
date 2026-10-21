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

Concord Label does not run models. It reads the platform's endpoint map through `aicore`, starts the
services a job needs with the platform's own `tools/stack.py`, and calls them over their contracts.

## Quickstart

```bash
# prerequisites: uv, just, Node 22+, ffmpeg; the platform repo at ../../platform (rendered)
just setup            # uv sync + npm install
just dev              # API :8620 + worker + Vite :5174 -> open http://localhost:5174
just test             # backend + frontend unit tests, no GPU or platform needed
just lint             # ruff, import-linter (concord_label must not import concord_improve), tsc
just e2e              # Playwright on replayed fixtures (Docker; needs the concord-label image, `just build`)
just e2e-handoff      # the round trip with Concord Bench: both images on one Docker network (see Two apps)
just check-contracts  # check contracts/samples and compare with Concord Bench next to this repository
```

`CONCORD_PORT` and `CONCORD_WEB_PORT` change the ports. `just api` serves the built frontend
(`just build-web`) from the API port, without Vite.

Without a GPU, `REPLAY_DIR=fixtures/recorded AICORE_ENDPOINTS=fixtures/recorded/endpoints.generated.yaml just dev`
serves recorded model responses for the fixture set, so the whole UI works offline.

To try it on the fixture set: Models → Add model for `pyannote-diarization`, `whisper-large-v3` and
`faster-whisper-large-v3`; Datasets → Upload dataset → Audio only → Local path →
`<repo>/fixtures/generated/dataset/audio`; then Pseudo-label that dataset and review it in Annotate.

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
  registry/    platform registry reader, compatibility check
  platform/    health, image tags, ensure_services() -> stack.py up --holder, leases (renew, release)
  adapters/    openai_asr, openai_chat_audio, oip_segmenter; record / replay transport
  runs/        job queue + worker (ingest, check, label, export), output cache
  cuts.py      a dataset's usable files; segmenter output -> merged, padded ASR cuts
  metrics/     normalisers, wer (jiwer), der/jer (pyannote.metrics), vad (used by the compatibility check)
  label/       ensemble fusion, pseudo-label runs, console, annotate edits, live evaluation,
               label packages (sync), reference packages for Bench (refpkg), flags from Bench (flags)
  peer.py      Concord Bench: /api/config probe (cached), locked scores
  offline/     offline kits
  api/         FastAPI routers
  migrations/  Alembic (0001 initial schema, 0002 dataset uid + reference_export)
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
`core/`, `datasets/`, `registry/`, `platform/`, `adapters/`, `runs/cache.py`, `runs/jobs.py`,
`runs/worker.py`, `cuts.py` (`prepare_cuts`, `usable_files`), `metrics/` (`wer`, `der`, `normalisers`,
`vad`), `offline/`; in the frontend `components/` (`AudioPlayer`, `Timeline`, `timelineMath`, `TagInput`,
`icons`, `ui`), `lib/` and the Models, Add model, Contract, Jobs, Upload and Offline export pages.

## How it works

- **Datasets.** Upload a folder or zip (chunked), or point at a local folder: audio alone, or audio with
  complete or partial annotations. Originals are copied to `CONCORD_DATA/originals/<sha256>`; models and
  playback use a derived 16 kHz mono FLAC. Files over 5 minutes are flagged and kept out of label runs.
  Annotations are matched to audio by file stem and become confirmed segments: ASR (JSONL / NeMo
  manifest / CSV / `.txt`), diarisation (RTTM / JSON segments / NeMo diarisation manifest plus its RTTMs),
  speaker-attributed transcripts (JSON/JSONL segments, STM, SegLST, NeMo diarisation output JSON).
  Templates are under `fixtures/generated/templates`.
- **Models.** Pick a registry entry; the compatibility check runs it on a synthesised 10 s two-speaker
  clip and detects capabilities (ASR / VAD / diarisation) and timing (word / segment / text). Nothing has
  to be typed.
- **Jobs.** One worker process works through the `job` table: ingest, compatibility check, label run,
  offline kit, Send to Bench. Before a job it asks the platform for the job's services (plus those of
  queued jobs that fit next to them under `util_limit`) with `stack.py up NAME... --holder label:job-<id>`,
  even when they are already up: that leases them, so Concord Bench's worker never stops them mid-run.
  The lease is renewed every minute (`stack.py lease`) and released when the job ends (`stack.py
  release`). If the platform can't start a service (or it does not fit next to services Bench has
  leased), the job is `blocked` with the platform's message. Every model call goes through the output cache (model, audio, span, params incl.
  image tag).
- **Console** (`/label`): label progress per dataset in audio time (confirmed / auto-accepted / needs
  review / rejected / running / not labelled) and the next action.
- **Pseudo-label**: segments come from one VAD / diarisation model; close or short same-speaker segments
  are merged and each cut is padded by 0.1 s (`cuts.prepare_cuts`, the same cuts Concord Bench's
  pipelines use); every ASR member transcribes each cut. Outputs saved by earlier label runs are reused,
  so re-fusing needs no service at all. Fusion: best-aligned hypothesis (default), ROVER over word-timed
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
  0.25 s each side). With Concord Bench reachable, each model's locked score there (newest version of
  this dataset, CONTRACTS.md §4) shows next to its live WER; otherwise the column is hidden.
- **Send to Bench** (live evaluation and dataset pages; replaces the single-app Promote): a reference
  package (CONTRACTS.md §2) of confirmed segments only. Scope `complete` takes fully reviewed files (ASR +
  DER), `any` every file with a confirmed segment (ASR only). The references file follows what the
  segments carry (SegLST, JSONL or RTTM), with segment uids so flags can come back; audio auto / always /
  never; optionally the segmenter's whole-file outputs with their full params and image. With `BENCH_URL`
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
`AI_HOST`, `NO_EVICT`, `REPLAY_DIR`, `RECORD_DIR`, `CONCORD_HOME`, `CONCORD_IMAGE`, `BENCH_URL` (Concord
Bench, unset by default; e.g. `http://localhost:8600`, or `http://concord-bench:8600` on a shared Docker
network).

Label's schema versions start with `label_`, and Label refuses to start (the API, the worker and
`just migrate`) on a database it did not write — Concord Bench's, or one from the single Concord app
before the split — without changing it. Point `CONCORD_DATA` at an empty folder to start fresh.

## Fixtures and recordings

Concord Bench owns the fixture sources. It regenerates `fixtures/generated` (`just fixtures`) and
re-records `fixtures/recorded` against live services (`just record`). When either changes there, copy the
folder into this repository. Label's tests and the e2e server use both: they register the fixture models
from recorded compatibility checks and pseudo-label the fixture audio on recorded responses.

## Image

`just build` builds `concord-label:0.2.0` (API and worker on port 8620). Improve is not in it. To run it
next to the platform:

```bash
docker run -d --name concord-label --network ai-net -p 127.0.0.1:8620:8620 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(realpath ../../platform)":/platform -v concord-label-data:/data concord-label:0.2.0
```

`AI_HOST={name}` is set in the image, so `aicore` calls services by name on `ai-net`. stack.py runs
compose against the host's Docker daemon with paths from the mounted platform directory, so mount the
platform at the same absolute path it has on the host if your compose file uses relative bind mounts. In
development, run Concord Label on the host instead.

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
- `./install.sh --add <install>` merges a model kit into an install. Register models there with Add
  model: no registrations or datasets travel; labels come back as label packages.
- The air-gap test of a kit (`just test-offline`, with Bench alone and with both images) lives in Concord
  Bench.

## Status

Milestones S2, S4, S5, S6 and S7 of SPLIT_PLAN.md (Label side): Label runs on its own, sends references to
Bench, links with it, leases its services, writes offline kits with either or both apps, and the round trip
with the `concord-bench` image passes (`just e2e-handoff`, SPLIT_PLAN.md §10). The split is built.

Known limits:
- KWS is not built (Q6). Only its hooks exist: the enums, the `keyword_event` table, the disabled
  Keywords focus in Annotate and an empty `keywords.jsonl` in label packages.
- Label packages do not carry deletions: a segment deleted on one side comes back if the other side
  still has it.
- Improve (Phase 3) is on hold; `backend/concord_improve` is a placeholder installed in no image.
- The fixture set is synthesised speech (kokoro-tts): good for correctness, not realism.
