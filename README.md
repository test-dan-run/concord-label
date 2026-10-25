# Concord Label

Concord Label is a web app for labelling speech audio. Models label the audio first, and then a person
checks and corrects the labels in the browser, so that the labels can serve as references for scoring
models. The labels are transcripts (ASR, automatic speech recognition) and speaker turns (diarisation,
which means working out who speaks when).

The app doesn't host models itself. It uses the Model Catalog in the platform repository
(`../../platform/catalog/README.md`), which registers, checks and runs the models. When it is set up, the
app also uses the platform's Dataset Catalog, which keeps the audio once for Label and Concord Bench and
holds the versions of each dataset that Label publishes.

## Quickstart

You need uv, just, Node 22 or later, ffmpeg, and the platform repository at `../../platform`. For real
inference, start the catalog service with `just catalog-up` in the platform repository. It serves both
the Model Catalog and the Dataset Catalog.

```bash
just setup   # install the Python and Node dependencies
just dev     # API on :8620, the worker, and Vite on :5174; open http://localhost:5174
just test    # backend and frontend unit tests
just lint    # ruff, import-linter and tsc
```

To run the app without a GPU or a Model Catalog, replay the recorded model responses for the fixture
set:

```bash
REPLAY_DIR=fixtures/recorded AICORE_ENDPOINTS=fixtures/recorded/endpoints.generated.yaml just dev
```

[.env.example](.env.example) lists the settings.

## Features

### Datasets

You upload a dataset as a folder or a zip, or point the app at a local folder. A dataset holds audio
alone, or audio with complete or partial annotations, and the annotations count as confirmed labels
straight away. The app reads these annotation formats:

- ASR transcripts: JSONL, a NeMo manifest, CSV, or `.txt`.
- Diarisation: RTTM, JSON segments, or a NeMo diarisation manifest.
- Speaker-attributed transcripts: JSON or JSONL segments, STM, SegLST, or NeMo diarisation output.

Templates for each format are in `fixtures/generated/templates`.

### Dataset Catalog

The Dataset Catalog is on when `DATASET_STORE` names the catalog's store, as this app sees it. Label only
reads the store, and it sends new audio to the catalog service at `CATALOG_URL`. With the Dataset Catalog
on, these things change:

- **Audio.** Each file you upload goes to the store, and Label places a hold on it, so the catalog keeps
  it. The 16 kHz copy and the waveform peaks stay in Label's own data folder. Deleting a dataset releases
  the holds, unless another dataset in Label uses the same audio. While the catalog service doesn't
  answer, a delete stops and the dataset stays, so no hold is lost.
- **Publish to Dataset Catalog.** On a dataset's page or on Live evaluation, this panel publishes the
  confirmed segments as a new version of the dataset in the catalog. The first publish makes the dataset
  in the catalog, and each later publish makes the next version. If someone published a newer version
  after the one your dataset is based on, the panel says who published it and when, and it asks before
  you publish anyway. The history lists each version, with a link to it in the catalog. Concord Bench
  imports the versions from there.
- **New from Dataset Catalog.** On Datasets, this makes a dataset from a published version. The files
  point at the audio in the store, and the references become confirmed segments, with the same segment ids
  as before, so flags from Bench still find them. A version with audio only becomes a dataset that is
  ready to pseudo-label. The next publish of the dataset becomes the next version of the same catalog
  dataset.
- **Flags and links from Bench.** Flags, "Open in Label" and the locked score in Live evaluation find a
  dataset by its catalog dataset id. "Open in Label" also works from a version's page in the Dataset
  Catalog. When no dataset here was made from that catalog dataset, the page points to New from Dataset
  Catalog.

Without the Dataset Catalog, the panel is Download references, which builds a reference package zip. Label
no longer sends datasets to Bench directly.

To move the audio of existing datasets into the store, run `just move-originals`. It uploads every file in
`data/originals/` to the store, holds each one, and renames the folder to `data/originals.moved/`. It
renames nothing unless every file reached the store. It never deletes audio, so delete
`data/originals.moved/` yourself when you're satisfied.

### Models

The Models page lists the models in the Model Catalog, with each model's state, timing level and cold
start time. The models are grouped as ASR, VAD and diarisation, and speech enhancement. The app doesn't
run speech enhancement models yet, so that group is for reference only.

### Pseudo-labelling

A pseudo-label run labels a dataset with an ensemble, which is a group of models whose outputs are
combined. The run has these steps:

1. One VAD or diarisation model finds the speech segments and the speakers.
2. Every ASR model in the ensemble transcribes each segment.
3. The app combines the transcripts into one starting transcript for each segment, and it scores each
   segment by how closely the models agree.

A segment with a score of 0.85 or more is auto-accepted, a segment from 0.50 to 0.84 needs review, and a
segment below 0.50 is rejected. The app saves every model output and reuses it, so combining the
transcripts again needs no model.

### Console

The console shows labelling progress for each dataset in audio time, and the next action for each
dataset.

### Annotate

Annotate shows one file at a time, with a timeline of the whole file above a table of its segments. You
can edit segments on the speaker lanes under the waveform:

- Drag a segment to move it, or drag it onto another lane to give it to that speaker.
- Drag a segment's grips to trim it, or use the arrow keys on a grip.
- Drag across empty space in a lane to draw a segment, and use Add speaker to add lanes, up to 8
  speakers.

The player above the timeline plays the file from the playhead at a speed from 0.5× to 2×, and the
segment under the playhead stands out on the lanes and in the table. You can zoom the timeline down to a
one-second window, and the mouse wheel moves along the file. You can drag across the waveform to play any
stretch of the file, including a stretch that crosses speakers. In the segment table, you edit
transcripts, change speakers, merge and split segments, and confirm segments one at a time or in bulk.

You can undo and redo any change to a file's segments, and Label keeps the last 200 changes for each
file. A pseudo-label run or an imported package clears that history for the files it changes.

Any edit marks a segment as checked by a person, and later pseudo-label runs never replace it. The Docs
page in the app lists the keyboard shortcuts.

### Lexicons

A lexicon sets one house spelling for a word that people write in more than one way, e.g., "lah"
instead of "la". Every lexicon applies to every dataset. Annotate underlines the variants in
transcripts, and you choose whether to use the house spelling or keep the text. You edit a lexicon as a
draft, and you publish it as a new version.

### Live evaluation

Live evaluation scores each model against the confirmed segments, using saved outputs only, so it never
runs a model. It shows WER (word error rate) with 95% intervals, and it shows DER (diarisation error
rate) and JER (Jaccard error rate) on fully reviewed files.

### Sync

Sync moves labelling progress between installs of the app as label packages, and the packages also carry
the lexicons. The app shows a preview of an import before you apply it, and pseudo-labels never
overwrite reviewed work. When both sides reviewed a segment differently, the app lists a conflict for
you to resolve, and you can undo an import.

### Jobs

Ingests, pseudo-label runs, publishes and datasets made from the Dataset Catalog run in the background,
one at a time, and the Jobs page shows their progress and logs. A pseudo-label run waits while the Model
Catalog queues it for compute.

While a pseudo-label run works, the Pseudo-label page and the Jobs page show its stage, how many files or
segments the stage has done, and about how long the run has left. The estimate comes from how fast each
model runs. It uses the speed a stage measures while it runs, and before that it uses the model's earlier
outputs. It adds the time to start a model that isn't running. It doesn't count time spent waiting for
compute, and it shows "estimating time left" until it knows the speed of every model with work to do.

You can cancel a pseudo-label run on the Pseudo-label page or on the Jobs page. The run stops before its
next segment and writes no new segments. It keeps the model outputs of the files it finished, so a retry
skips them. A cancelled run that waits for compute gives up its place in the Model Catalog's queue.

An ASR model sometimes fails on one segment. The option "Skip a segment when an ASR model fails on it" is
on by default. With it, the run goes on, and the other models still transcribe that segment. The results
list each skipped segment with the model's error, and a retry tries those segments again. A model that
fails on more than 10 segments and more than 5% of its segments still stops the run. With the option off,
the run stops at the first error.

### Docs

The Docs page under Help explains the workflow, the segment statuses and the keyboard shortcuts.

## Known limitations

- Features that aren't built:
  - Keyword spotting (KWS) isn't built yet.
- Behaviour:
  - Each file, or each segment of a file, goes to a model in one request, so a very long file works
    only if the model accepts it.
  - Label packages don't carry deletions, so a segment deleted on one side comes back if the other
    side still has it.
  - The worker runs one job at a time, so while a pseudo-label run waits for compute, later jobs wait
    behind it.
  - The waveform has 100 peaks per second, so it looks blocky at the highest zoom, although segment
    times are kept to the millisecond.
  - New from Dataset Catalog reads the file labels in a version's `refs/attributes.json`, but not its
    speaker labels.
  - Audio that arrives through Sync is kept in `data/originals/`, as before. The next publish, or
    `just move-originals`, also sends it to the store.
  - When Label runs in Docker, it registers with the catalog under its container name, which a browser
    can't open. So the Dataset Catalog's pages hide "Open in Label".
  - Opening `/docs` directly on the built app, e.g. from a bookmark, shows the API's Swagger page instead
    of the Docs page. Open the Docs page from the sidebar.
- Test data:
  - The fixture set for tests and replay is synthesised speech, so it's good for testing correctness
    but isn't realistic.
