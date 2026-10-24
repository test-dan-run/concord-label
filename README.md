# Concord Label

Concord Label is a web app for labelling speech audio. Models label the audio first, and then a person
checks and corrects the labels in the browser, so that the labels can serve as references for scoring
models. The labels are transcripts (ASR, automatic speech recognition) and speaker turns (diarisation,
which means working out who speaks when).

## Concord Label and Concord Bench

Concord Label is one of two apps that were split out of the original Concord app, as SPLIT_PLAN.md
describes:

- Concord Bench benchmarks models on reference datasets and analyses their errors. It runs on port 8600.
- Concord Label, this repository, produces those reference datasets. It runs on port 8620.

The two apps share no code, and each app has its own data directory and database. They agree only on the
file formats and endpoints in [CONTRACTS.md](CONTRACTS.md), which must be identical in both repositories,
and `just check-contracts` checks that. Every link between the apps is optional, so Label works on its own
when Bench isn't running.

Concord Label doesn't register, check, start or call models itself, because the model catalog in the
platform repository does that work (`../../platform/catalog/README.md`). Label lists the catalog's models,
and it leases each model from the catalog while a job needs it. Every inference call goes through the
catalog's gateway, using the `aicore` client. The catalog also keeps an index of model outputs, so Label
doesn't run a model again on audio that Label or Concord Bench has already run that model on.

## Quickstart

You need uv, just, Node 22 or later, and ffmpeg. You also need the platform repository at
`../../platform`, rendered. For real inference, the platform's model catalog must be running, and
`just catalog-up` in the platform repository serves it at http://127.0.0.1:8700.

```bash
just setup            # uv sync and npm install
just dev              # API on :8620, the worker, and Vite on :5174; open http://localhost:5174
just test             # backend and frontend unit tests; needs no GPU, platform or catalog
just lint             # ruff, import-linter (concord_label must not import concord_improve), and tsc
just e2e              # Playwright on replayed fixtures, in Docker; needs the image from `just build`
just e2e-handoff      # the round trip with Concord Bench, with both images on one Docker network
just check-contracts  # checks contracts/samples and compares them with the Concord Bench repository
```

`CONCORD_PORT` and `CONCORD_WEB_PORT` change the ports. After `just build-web`, `just api` serves the
built frontend from the API port, without Vite.

You can run the whole app without a GPU, because Label can replay recorded model responses for the
fixture set:

```bash
REPLAY_DIR=fixtures/recorded AICORE_ENDPOINTS=fixtures/recorded/endpoints.generated.yaml just dev
```

In replay mode you don't need the model catalog either. Label builds a stand-in catalog in memory from
the recordings, and it lists the fixture models as running.

To try the app on the fixture set, follow these steps:

1. Make sure that the catalog has `pyannote-diarization`, `whisper-large-v3` and
   `faster-whisper-large-v3`. In replay mode they're there already, and otherwise you register them in
   the catalog's own UI.
2. In Datasets, choose Upload dataset, then Audio only, then Local path, and enter
   `<repo>/fixtures/generated/dataset/audio`.
3. Pseudo-label the new dataset, and then review it in Annotate.

The database starts empty, and nothing is migrated from the single Concord app (SPLIT_PLAN.md §8).
Labelling work in progress moves over as a label package, which you import on the Sync page.

## Working with Concord Bench

In development you run both apps side by side. Run `just dev` here, which serves the API on 8620 and Vite
on 5174, and run `just dev` in the Bench repository, which uses 8600 and 5173. Each app finds the other
through one setting in its `.env`, which is `BENCH_URL=http://localhost:8600` here and
`LABEL_URL=http://localhost:8620` in Bench. When the setting is unset, the app hides the links to the
other app, and you download reference packages instead of pushing them.

Finished references go from Label to Bench as a reference package (CONTRACTS.md §2). Send to Bench, on a
dataset's page or on Live evaluation, writes a package of the dataset's confirmed segments. When Bench
answers, Label pushes the package and sends only the audio that Bench doesn't have yet. Otherwise you
download the package as a zip. In Bench, Import reference package shows a preview, and accepting the
package creates a dataset there. A later package of the same dataset becomes the next version of that
dataset in Bench.

Bench links back to Label in these ways:

- Open in Label, in Bench, opens the file in Label's annotator.
- Live evaluation, in Label, shows Bench's locked score for each model.
- Send flags to Label, in Bench, flags segments in Label for a person to check.

`just e2e-handoff` tests the round trip between the apps (`tests/test_handoff.py`, with the marker
`handoff`, which `just test` leaves out). First, build both images with `just build` in each repository.
The test then runs both images in replay mode on one Docker network and drives them through their APIs,
in these steps:

1. It benchmarks the fixture set directly in Bench.
2. It uploads the same set to Label and sends it to Bench twice, once by push, with no audio sent, and
   once by download. Bench accepts the two packages as versions 1 and 2.
3. It checks that the WER equals the direct benchmark each time, that Label shows the locked score, and
   that a flag comes back to Label.

At the end, the test removes everything it created.

Some other points about the two apps:

- Offline kits bundle the apps, the platform and models for a machine without internet access. They're
  now the model catalog's job, and Label no longer writes them.
- `just check-contracts` validates `contracts/samples`, and it compares `CONTRACTS.md` and `contracts/`
  with the Bench repository, which must match byte for byte. It looks for Bench next to this repository,
  as `../Concord` or `../concord-bench`, and you can pass `--peer PATH` instead.
- Bench owns `fixtures/generated` and `fixtures/recorded`. When Bench makes them again (`just fixtures`
  and `just record`), copy both folders into this repository.

## Layout

```
backend/concord_label/
  core/        settings, paths (the only module that knows where files live), SQLite models, db
  datasets/    audio (ffmpeg probe and decode, clipping, 16 kHz copy, peaks), annotation parsers, ingest
  platform/    catalog.py: the model catalog (models, leases, replay stand-in, app registration)
  adapters/    openai_asr, openai_chat_audio, oip_segmenter; record and replay transport
  runs/        job queue and worker (ingest, label, reference), output cache and the catalog's index
  cuts.py      a dataset's usable files; segmenter output to merged, padded ASR cuts
  metrics/     normalisers, wer (jiwer), der and jer (pyannote.metrics), vad
  label/       ensemble fusion, pseudo-label runs, console, annotate edits, live evaluation, lexicons,
               label packages (sync), reference packages for Bench (refpkg), flags from Bench (flags)
  peer.py      Concord Bench: the /api/config probe (cached) and locked scores
  api/         FastAPI routers
  migrations/  Alembic: 0001 initial schema, 0002 dataset uid and reference_export, 0003 no duration cap,
               0004 models from the catalog with outputs keyed on build key, 0005 no offline kits,
               0006 lexicons
backend/concord_improve/   Phase 3 placeholder router; on hold, and no image installs it
frontend/src/
  app/         routes and the sidebar
  features/    one folder per page (label, datasets, models, lexicons, jobs, docs)
  components/  shared parts: AudioPlayer, Timeline, ui, icons
  lib/         the API client and types, and helpers (format, label, lexicon)
contracts/     the contract samples and their checker (CONTRACTS.md); identical in Concord Bench
fixtures/      generated/ (audio, refs, templates, check clip) and recorded/ (model responses for
               replay), both copied from Concord Bench
tests/         pytest; `-m live` runs tests against running services, `-m handoff` the round trip with Bench
```

### Code copied from Concord Bench

Both apps started from the same code, and each app keeps its own copy of the shared modules
(SPLIT_PLAN.md §2.2 and §11). When you fix a bug in one of these modules, check the same module in Concord
Bench at the same time:

- Backend: `core/`, `datasets/`, `platform/`, `adapters/`, `runs/cache.py`, `runs/jobs.py`,
  `runs/worker.py`, `cuts.py` (`prepare_cuts` and `usable_files`), and `metrics/` (`wer`, `der`,
  `normalisers` and `vad`).
- Frontend: `components/` (`AudioPlayer`, `Timeline`, `timelineMath`, `TagInput`, `icons` and `ui`),
  `lib/`, and the Models, Jobs and Upload pages.

The adapters' params must stay identical in both apps, because the catalog's output index matches
outputs across the apps on the build key, the audio, the span and the params hash
(`aicore.catalog.params_hash`). Before the model catalog, the registry reader, the compatibility check,
and the Add model and Contract pages were shared copies too, and both apps replaced them with the
catalog's own UI.

Bench's copies don't have these recent Label changes yet:

- `AudioPlayer`: `useAudio` takes the `<audio>` element through a callback ref, so its play and pause
  listeners attach even when the element renders after the page loads its data. Without the fix, a page
  that renders the player late never learns that playback started, and Space restarts the audio instead
  of pausing it.
- `Timeline`: the optional `selection` and `onSelection` props let you select a stretch on the
  waveform, and the module exports `LABEL_W`, the width of the lane name column.
- Models page: it groups the models by capability.

## How it works

### Datasets

You can upload a folder or a zip, which goes up in chunks, or point Label at a local folder. A dataset
holds audio alone, or audio with complete or partial annotations. Label copies the original files to
`CONCORD_DATA/originals/<sha256>`, and models and playback use a 16 kHz mono FLAC copy of each file.

Files have no length limit, but each file, or each cut of a file, goes to a model in one request. A very
long file therefore works only if the model accepts it. For example, VAD and diarisation models receive
the audio as JSON numbers, which take about 1.25 GB per hour of audio.

Label matches annotations to audio files by the file name without its extension, and the annotations
become confirmed segments. Label reads these formats:

- ASR: JSONL, a NeMo manifest, CSV, or `.txt`.
- Diarisation: RTTM, JSON segments, or a NeMo diarisation manifest with its RTTM files.
- Speaker-attributed transcripts: JSON or JSONL segments, STM, SegLST, or NeMo diarisation output JSON.

Templates for each format are in `fixtures/generated/templates`.

### Models

The model catalog registers, checks and runs the models. To register a model, you pick a platform
service in the catalog's UI and add tags. The catalog's verifier then tests the model on a check clip,
and it records the model's capabilities, e.g., ASR, VAD, diarisation or speech enhancement. It also
records the model's timing level (word times, segment times, or text only) and its cold start time.

The catalog also records the build key of each model's current build. A build key identifies one build
of a model, made from its image, config and weights.

The Models page lists the catalog's models read-only, in groups for ASR, VAD and diarisation, and speech
enhancement. A model with two capabilities shows in both groups, and models that aren't verified yet, or
that do something else, show under Other. Label doesn't run speech enhancement models yet, so that group
is for reference only. The page links to the catalog's UI (`CATALOG_UI_URL`) for registering models.

Label keeps a `model` row for each catalog model it has used, because runs and outputs refer to model
ids. Label creates the row on first use and refreshes it from the catalog, so live evaluation and exports
still work while the catalog is down.

### Jobs

One worker process runs the jobs in the `job` table, which are ingests, label runs and sends to Bench.
Among the queued jobs, the worker takes the oldest job whose models are all running in the catalog, and
otherwise it takes the oldest job.

A label run leases its models from the catalog one stage at a time, under the holder name
`label:job-<id>`. The aicore client renews a lease every 30 seconds, and a lease expires 90 seconds
after a worker crashes. While the catalog queues a lease, the job is "waiting", and the sidebar and the
Jobs page show its place in the queue, e.g., "waiting for compute #2".

If a model fails to start, or the catalog doesn't answer, the job is "blocked" and shows the catalog's
message. Retry runs the job again, and it skips the work that is already saved.

### Model outputs and the output index

Every model call goes through the output cache in `runs/cache.py`. The cache key is made of the model,
the audio, the span, the params hash and the build key, and the params never include the image or the
build.

Label looks up outputs in batches, for each file or each stage, in this order:

1. Label checks its own rows.
2. Label asks the catalog's output index.
3. When Concord Bench already made an output, Label reads it from Bench (`POST /api/outputs/batch`) and
   copies it into its `model_output` table, noting where it came from (`source_app` and `source_id`).
   Label then records its copy in the index.

Label also records everything it computes in the index, and it serves `POST /api/outputs/batch` for
Bench. At startup, the API registers `PUBLIC_URL` with the catalog, and it keeps trying in the background
while the catalog is down.

A new build of a model means new outputs. Rows from an older build stay readable by id, but they never
match a lookup, and the same holds for rows from before the catalog, which have no build key. Label keeps
the outputs of a model whose build key the catalog doesn't know yet, but it never indexes or matches them.

### Console

The console (`/label`) shows labelling progress for each dataset in audio time. Progress is split into
confirmed, auto-accepted, needs review, rejected, running, and not labelled, and the console also shows
the next action for each dataset.

### Pseudo-labelling

A pseudo-label run labels a dataset with an ensemble, which is a group of models whose outputs are
combined. One VAD or diarisation model finds the speech segments. Label merges segments from the same
speaker that are close together or short, and it pads each resulting cut by 0.1 seconds. The cuts come
from `cuts.prepare_cuts`, which makes the same cuts as Concord Bench's pipelines. Every ASR model in the
ensemble then transcribes every cut.

A run goes stage by stage, so each model loads once, and only one model is leased at a time:

1. The segmenter runs over every file, under one lease.
2. Label makes the cuts.
3. Each ASR model runs over every cut, under its own lease. Label releases each lease before taking the
   next one, in the order of the catalog's plan.
4. Label fuses the transcripts, which needs no model.

Label reuses outputs that are saved here or in Bench, and a stage with nothing missing takes no lease, so
fusing saved outputs again needs no model at all. Label saves the outputs after each file, so a retried
run skips finished work.

The setup panel shows each model's state, capabilities and cold start time, and whether its outputs are
saved, saved in Bench, partly saved, or new. It also shows the catalog's plan, with the number of model
loads and the estimated cold start time.

Fusion picks one starting transcript for each segment in one of these ways:

- The best-aligned hypothesis, which is the default, takes the transcript that agrees most with the
  others.
- ROVER combines the transcripts of the models that give word times by voting word by word.
- One model takes the transcript of a model that you choose.

Confidence is the agreement between the models. A segment with a confidence of 0.85 or more is
auto-accepted, a segment from 0.50 to 0.84 needs review, and a segment below 0.50 is rejected. Label
stores pseudo-labels in the `segment` table, with the label run as `source_run_id`.

### Annotate

Annotate shows one file at a time, with a timeline of the whole file above a table of its segments. The
timeline has the waveform, one lane per speaker, a hatch where speakers overlap, and a lane for flags.
You can edit segments on the speaker lanes in these ways:

- Drag a segment to move it, or drag it onto another lane to give it to that speaker.
- Drag a segment's grips to trim it, or give a grip the focus and use the arrow keys.
- Drag across empty space in a lane to draw a segment, and use Add speaker to add lanes, up to 8
  speakers.

Label saves each change once, when you release the mouse.

You can zoom the timeline down to a one-second window, with the zoom buttons, with the `=`, `-` and `0`
keys, or with Ctrl (⌘ on a Mac) and the scroll wheel at the pointer. To move along the file, hold Shift
and scroll, or drag the window on the overview strip. The view follows playback and the selected
segment.

To listen to any stretch of the file, including a stretch that crosses speakers, drag across the
waveform, and the stretch plays at once. Space plays it again, and Escape or a click on the waveform
clears it.

The segment table supports multi-select with Shift-click, with a bar of bulk actions. You can edit a
transcript in place, and it saves automatically. Clicking a segment's speaker cycles through the speakers,
and you can merge, split and add segments. The keyboard shortcuts are Space, Tab, Alt+Enter, 1 to 8, V,
M and Enter, and the Docs page in the app lists what each one does.

Any edit marks the segment as touched by a person, and pseudo-labelling never replaces a touched segment
afterwards. A segment drawn by hand shows the status "New · manual" until someone confirms it.

Annotate underlines the variants that a published lexicon flags, and it offers a choice to use the house
spelling or keep the text. Alt+Enter uses the house spelling. When you keep a spelling, Label stores the
choice on the segment (`meta.spelling_kept`) and doesn't show that hint again.

### Lexicons

A lexicon lists house spellings, and for each house spelling the variant spellings to flag. Every lexicon
applies to every dataset. You edit a lexicon's entries as a draft, and publishing the draft makes the
next version, and Annotate uses each lexicon's latest published version.

Label refuses to publish a version while a variant would get two different house spellings, or while a
variant is another entry's house spelling. Each entry shows how often its house spelling and its variants
appear in confirmed segments, with a link to the first confirmed segment that has a variant.

Matching ignores case and works on whole words, and the longest term wins. The server matches in
`label/lexicon.py` and the browser in `lib/lexicon.ts`, and both are tested on the same cases.

### Live evaluation

Live evaluation scores each model against the confirmed segments, using only saved outputs. It shows
WER (word error rate), with 95% intervals from bootstrapping, and it shows "too few to rank" below 30
confirmed segments. It also shows DER (diarisation error rate) on fully reviewed files, and the latest
confirmations.

Label computes WER with jiwer after a normaliser. The normaliser is `basic` by default, and the other
choices are `whisper_en`, `none`, or any platform `text.transform` service followed by `basic`. Label
computes DER and JER (Jaccard error rate) with pyannote.metrics, with a collar of 0.25 seconds on each
side by default. Only outputs of each model's current build count.

When Concord Bench is reachable, Live evaluation shows each model's locked score in Bench next to its
live WER, from the newest version of the dataset in Bench (CONTRACTS.md §4). Otherwise the column is
hidden.

### Send to Bench

Send to Bench, on Live evaluation and on a dataset's page, writes a reference package (CONTRACTS.md §2)
of confirmed segments only. It replaces Promote from the single Concord app.

The scope `complete` takes only fully reviewed files, for both ASR and DER, and the scope `any` takes
every file with a confirmed segment, for ASR only. The references file uses the format that the segments
carry (SegLST, JSONL or RTTM), with segment uids so that flags can come back. You can include audio
automatically, always, or never. You can also include the segmenter's outputs for whole files, with
their full params, image and build key, and Bench matches them on the build key.

When `BENCH_URL` is reachable and Bench is on the same contract version, Label pushes the package, in
these steps:

1. Label asks Bench which audio files it lacks.
2. Label uploads the zip in 8 MB chunks.
3. Bench stages the package as a reference import, which you accept in Bench.

Otherwise you download the package as a zip. Every send is a new `export_version`, listed on the
dataset's page.

### Links from Bench

`/label/file/by-sha/<sha256>?dataset_uid=<uid>&t=<s>` opens the annotator on a file at the time `t`.
`POST /api/flags/import`, or a flags file uploaded on the dataset's page, flags segments. Label matches
flags to segments by uid or by overlap, and the flags show in the annotator's flags lane.

### Sync

Sync moves labelling progress between Label installs as label packages. A package contains
`manifest.json`, `files.csv`, `segments.jsonl`, `keywords.jsonl` and `lexicons.json`, and it can also
contain `runs/` and `audio/`.

Label stages and previews an import before applying it. Files are matched by their SHA-256 fingerprint,
and segments are matched by `uid` or by time overlap. The merge follows these rules:

- Pseudo-labels never overwrite reviewed work.
- When both sides reviewed a segment differently, the segment becomes a conflict for you to resolve.
- Lexicons are matched by uid. Label takes a newer version and keeps an older one, and the same version
  with different entries is a conflict.

Label takes a snapshot before the merge, so you can undo an import, and undo also removes the lexicons
and versions that the import added.

### Config endpoint

`GET /api/config` reports `app` (`concord-label`), `version`, `contract_version` and `peer`
(CONTRACTS.md §1). `peer` holds `BENCH_URL`, whether Bench answered its own `/api/config`, and Bench's
contract version. The check has a timeout of 1 second, and its answer is cached for 30 seconds.

## Settings

You set these as environment variables or in `.env`, as [.env.example](.env.example) shows:

- General: `CONCORD_DATA` (default `data/` in this repository), `CONCORD_PORT` (8620),
  `CONCORD_WEB_PORT` (5174), `CONCORD_HOME`, `PLATFORM_DIR`, `AICORE_ENDPOINTS` and `AI_HOST`.
- Recordings: `REPLAY_DIR` serves recorded model responses, and `RECORD_DIR` writes every live response
  to a folder.
- Concord Bench: `BENCH_URL` is unset by default, e.g., `http://localhost:8600`, or
  `http://concord-bench:8600` on a shared Docker network.
- Model catalog:
  - `CATALOG_URL` or `AICORE_CATALOG` (default `http://127.0.0.1:8700`, and `http://catalog:8700` in the
    image). Label exports it as `AICORE_CATALOG`, so `aicore.client(name)` goes through the catalog's
    gateway.
  - `CATALOG_UI_URL` is where the Models page links for registering models, and it defaults to the
    catalog URL.
  - `PUBLIC_URL` is where the catalog and Bench reach this API for `POST /api/outputs/batch` (default
    `http://127.0.0.1:<CONCORD_PORT>`, and `http://concord-label:8620` in the image).

`NO_EVICT` and the service start timeout are gone, because the catalog's scheduler decides which models
run.

Label's schema versions start with `label_`. Label refuses to start the API, the worker or
`just migrate` on a database that it didn't write, e.g., Concord Bench's database or one from the single
Concord app, and it leaves that database unchanged. To start fresh, point `CONCORD_DATA` at an empty
folder.

## Fixtures and recordings

Concord Bench owns the fixture sources. It makes `fixtures/generated` again with `just fixtures`, and it
records `fixtures/recorded` again against live services with `just record`. When either folder changes in
Bench, copy it into this repository.

Label's tests and the e2e server use both folders. The replay catalog lists the fixture models with the
capabilities recorded in `fixtures/recorded/summary.json`, and the tests pseudo-label the fixture audio
with the recorded responses.

Recordings are named by the hash of the adapter params. The params lost `image` when the model catalog
arrived, so `fixtures/rekey_recordings.py` renamed the existing recordings instead of recording them
again. If Bench's recordings still carry `image`, run the script on a copy from Bench.

## Docker image

`just build` builds `concord-label:0.2.0`, which runs the API and the worker on port 8620. Improve isn't
in the image. To run the image next to the platform, use this command:

```bash
docker run -d --name concord-label --network ai-net -p 127.0.0.1:8620:8620 \
  -v "$(realpath ../../platform)":/platform -v concord-label-data:/data concord-label:0.2.0
```

The image sets `CATALOG_URL=http://catalog:8700` and `PUBLIC_URL=http://concord-label:8620`, so on the
`ai-net` network it finds the catalog, and the catalog finds it. You start the catalog with
`just catalog-up` in the platform repository. Every inference call goes through the catalog's gateway,
and the platform mount gives aicore the platform's endpoint map (`AICORE_ENDPOINTS`). In development, run
Concord Label on the host instead.

## Status

Label has reached milestones S2, S4, S5, S6 and S7 of SPLIT_PLAN.md, so the split is built. Label runs on
its own, sends references to Bench and links with it, and the round trip with the `concord-bench` image
passes (`just e2e-handoff`, SPLIT_PLAN.md §10). Since then, models, leases, inference and offline kits
have moved to the platform's model catalog (MODEL_CATALOG_REDESIGN.md in the platform repository).

Label has also gained these features from the updated design canvas (HANDOFF.md §4):

- In Annotate, you can edit segments on the speaker lanes, zoom the timeline, and play a stretch that
  you select on the waveform.
- Lexicons set house spellings, show spelling hints in Annotate, and travel in label packages.
- A Docs page sits under Help, and the Models page groups the models by capability.

Known limits:

- Features that aren't built:
  - KWS (keyword spotting) isn't built (Q6). Only its hooks exist, which are the enums, the
    `keyword_event` table, the disabled Keywords focus in Annotate, and an empty `keywords.jsonl` in
    label packages.
  - Improve (Phase 3) is on hold, and `backend/concord_improve` is a placeholder that no image installs.
- Behaviour:
  - Label packages don't carry deletions, so a segment deleted on one side comes back if the other side
    still has it.
  - The worker runs one job at a time, so while a label run waits in the catalog's queue, later jobs,
    e.g., an ingest, wait behind it.
  - The waveform has 100 peaks per second, so it looks blocky at the highest zoom, although segment
    times are kept to the millisecond.
- Data and tooling:
  - The fixture set is synthesised speech (kokoro-tts), which is good for testing correctness but isn't
    realistic.
  - `CONTRACTS.md` here no longer has §6 (apps in an offline kit), so `just check-contracts` reports a
    difference until Concord Bench removes §6 and `contracts/samples/kit` too.
