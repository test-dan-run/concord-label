# Concord Label

Concord Label is a web app for labelling speech audio. Models label the audio first, and then a person
checks and corrects the labels in the browser, so that the labels can serve as references for scoring
models. The labels are transcripts (ASR, automatic speech recognition) and speaker turns (diarisation,
which means working out who speaks when).

The app doesn't host models itself. It uses the model catalog in the platform repository
(`../../platform/catalog/README.md`), which registers, checks and runs the models.

## Quickstart

You need uv, just, Node 22 or later, ffmpeg, and the platform repository at `../../platform`. For real
inference, start the model catalog with `just catalog-up` in the platform repository.

```bash
just setup   # install the Python and Node dependencies
just dev     # API on :8620, the worker, and Vite on :5174; open http://localhost:5174
just test    # backend and frontend unit tests
just lint    # ruff, import-linter and tsc
```

To run the app without a GPU or a model catalog, replay the recorded model responses for the fixture
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

### Models

The Models page lists the models in the model catalog, with each model's state, timing level and cold
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

You can zoom the timeline down to a one-second window, and you can drag across the waveform to play any
stretch of the file, including a stretch that crosses speakers. In the segment table, you edit
transcripts, change speakers, merge and split segments, and confirm segments one at a time or in bulk.

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

Ingests and pseudo-label runs run in the background, one at a time, and the Jobs page shows their
progress and logs. A pseudo-label run waits while the model catalog queues it for compute.

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
- Test data:
  - The fixture set for tests and replay is synthesised speech, so it's good for testing correctness
    but isn't realistic.
