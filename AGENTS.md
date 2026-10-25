# Notes for coding agents

Concord Label is a web app for labelling speech audio, with a FastAPI backend in `backend/concord_label`
and a React frontend in `frontend/src`. [README.md](README.md) describes the app's features for its
users, and it leaves out the developer details that this file covers.

The other docs are in `docs/`, and code comments cite them by file name, e.g., "CONTRACTS.md §2":

- [docs/HANDOFF.md](docs/HANDOFF.md) describes the behaviour the owner asked for. The design canvas it
  links shows every screen, and where the canvas and HANDOFF.md disagree, HANDOFF.md wins.
- [docs/CONTRACTS.md](docs/CONTRACTS.md) holds the formats and endpoints that Concord Label and Concord
  Bench agree on.
- [docs/ANNOTATE_PLAN.md](docs/ANNOTATE_PLAN.md) brought the annotating interactions of the owner's earlier
  app, pseudolabel, into Annotate. Where it differs from HANDOFF.md, the plan's decisions win.
- [docs/PLAN.md](docs/PLAN.md) and [docs/SPLIT_PLAN.md](docs/SPLIT_PLAN.md) are the original build plans,
  kept for history.

## Commands

```bash
just setup            # uv sync and npm install
just dev              # API on :8620, the worker, and Vite on :5174
just test             # pytest, then the frontend unit tests (vitest)
just lint             # ruff, import-linter, and tsc
just check-contracts  # contracts/samples, and the model adapters against the platform's contracts
just move-originals   # move data/originals/ into the Dataset Catalog's store (DATASET_STORE)
```

Until the platform's `dataset-catalog` branch merges, the `aicore` that `uv sync` installs from
`../../platform` has no `aicore.datasets`. To work against the Dataset Catalog before then, install the
client from the platform branch's worktree into this app's environment, e.g.
`uv pip install -e <platform worktree>/clients/aicore`. Then set `UV_NO_SYNC=1` for every `just` and
`uv run` command, so uv doesn't put the old client back, and set `PLATFORM_DIR` to the same worktree for
`just build`, `just check-contracts` and `just e2e-handoff`. Never run a plain `uv sync` or `just setup`
while you work this way. Label runs without the Dataset Catalog on the old client, as long as
`DATASET_STORE` isn't set.

You can run the Playwright tests without Docker. First, run `npm run build` in `frontend/`. Second, run
`npx playwright test` there, and the Playwright config starts `tests/e2e_server.py` on port 8621 with a
fresh data folder and replayed model responses. `just e2e` runs the same tests in Docker, and it needs
the image from `just build`.

Run `just test` and `just lint` before you commit. For a change to the UI, also run the Playwright
tests, and look at the page in a browser, because several bugs in Annotate only showed up there.

## Rules for changes

- Schema changes need an Alembic migration in `backend/concord_label/migrations/versions/`, with the
  next `label_000N` id (`db.next_revision_id()`). Update the pinned revision ids in
  `tests/test_db_owner.py`, and `tests/test_migrations.py` checks that the head schema matches the ORM
  models.
- How Label talks to models follows the platform's contracts in `PLATFORM_DIR/contracts` (the
  `openai`, `oip-v2` and `openai-inspired` contracts, and the registry in `api_types.yaml`).
  `tests/test_platform_contracts.py` runs the platform's own conformance checks against the fake services
  in `tests/platform_fakes.py`, and then runs Label's adapters and its text normaliser against the same
  fakes. When the platform changes a contract, update the fake until the conformance checks pass, and
  then fix the adapter the test shows is broken. A new adapter needs a fake and an entry in the test.
- `docs/CONTRACTS.md` describes the formats Label exchanges with Concord Bench, and `contracts/check.py`
  checks Label's samples of them. The file and `contracts/` must stay identical to Bench's `CONTRACTS.md`
  and `contracts/`, but no check compares them, so copy a change to Bench by hand and tell the owner.
  Contract version 3 moves datasets through the Dataset Catalog: Label publishes versions and no longer
  pushes to Bench, and flags, links and scores name a dataset by its `catalog_dataset_id`.
- Some modules are copies of Concord Bench's code, listed under "Code shared with Concord Bench" below.
  When you fix one of them, note the fix for Bench, and keep the adapters' params identical in both
  apps.
- The lexicon matcher exists twice, as `backend/concord_label/label/lexicon.py` and
  `frontend/src/lib/lexicon.ts`. Change both together, and keep `tests/test_lexicons.py` and
  `frontend/src/lib/lexicon.test.ts` on the same cases.
- Each edit a person makes to segments records itself in the file's history (`label/history.py`), so
  Annotate can undo it. A new edit in `label/edit.py` takes a `history.snapshot` of the segments before
  and after, and calls `_record`. Code that changes segments for any other reason, e.g., a pseudo-label run
  or a package import, calls `history.forget` for the files it changes.
- Annotate's keyboard shortcuts are listed once, in `SHORTCUTS` in `frontend/src/lib/label.ts`. The
  strip under the segment table and the Docs page both read that list, so add a new shortcut there.
- `components/Timeline.tsx` is shared with Bench and draws the waveform and the axis. The speaker lane
  editing in Annotate lives in `features/label/SpeakerLanes.tsx`, the drag maths in `laneDrag.ts`, and
  the zoom maths in `zoom.ts`, and all three are Label's own.
- Offline kits now belong to the platform's Model Catalog, so don't add offline export back to Label.
- The Dataset Catalog's store belongs to the catalog service. Label reads audio and version folders from
  the store's mount but never writes there. It sends audio through `platform/datasets.py`, and it places
  a hold on every file one of its datasets uses, so the catalog's clean-up never deletes it.
- Match the style of the code around your change, including how many comments it has.

## Code shared with Concord Bench

Concord Label and Concord Bench started from the same code, and each app keeps its own copy of these
modules:

- Backend: `core/`, `datasets/`, `platform/`, `adapters/`, `runs/cache.py`, `runs/jobs.py`,
  `runs/worker.py`, `cuts.py` (`prepare_cuts` and `usable_files`), and `metrics/` (`wer`, `der`,
  `normalisers`, `vad` and `cpwer`). Label copied `metrics/cpwer.py` from Bench unchanged for the cpWER
  column on Live evaluation.
- Frontend: `components/` (`AudioPlayer`, `Timeline`, `timelineMath`, `TagInput`, `icons` and `ui`),
  `lib/`, and the Models, Jobs and Upload pages.

The adapters' params must stay identical in both apps, because the Model Catalog's output index matches
outputs across the apps on the build key, the audio, the span and the params hash
(`aicore.catalog.params_hash`).

Bench's copies don't have these recent Label changes yet:

- `AudioPlayer`: `useAudio` takes the `<audio>` element through a callback ref, so its play and pause
  listeners attach even when the element renders after the page loads its data.
- `AudioPlayer`: `useAudio` has `skip(seconds)`, and a seek ends the playback of a span, so a segment
  started with its play button stops when you click somewhere else. Annotate renders its own `<audio>` in
  `features/label/Transport.tsx` and no longer uses `<AudioPlayer>`.
- `Timeline`: the optional `selection` and `onSelection` props, and the exported `LABEL_W`.
- Models page: the groups by capability.
- `JobPill` and `StatusDot`: the `cancelling` and `cancelled` states, and `jobStatusText` says "cancelling".
  `paths.job_cancel` and the cancel in `runs/jobs.py` are Label's, because Bench has no pseudo-label jobs.
- `lib/format.ts`: `duration`, `etaText` and `stageText`, and `lib/api.ts`: `JobEta`. The estimate itself
  (`label/eta.py`) is Label's.
- `runs/cache.py`: `OutputCache.run` is split into `infer`, which makes the call, and `store`, which saves the
  output. Worker threads call `infer`, and the session stays on one thread. Pseudo-labelling keeps several
  calls open at once (`in_flight` in `label/pseudo.py`), so the model's server batches them.
- `runs/cache.py`: `outputs_for` reads its ids in chunks of 500. SQLite takes at most 32766 variables in one
  query, and fusing 4 members on 8341 cuts needed 33364.
- `core/settings.py`: a blank `AICORE_ENDPOINTS`, `REPLAY_DIR` or `RECORD_DIR` in `.env` means unset. Bench
  does this for `DATASET_STORE` only, so a blank `RECORD_DIR=` there still records into the current folder.

The Dataset Catalog work (the platform's DATASET_CATALOG_PLAN.md, milestones D4 to D6) changed some shared
modules in both apps, and each app made its own version of the change. These modules now differ on
purpose, so compare them by hand before you copy a fix from one app to the other:

- `core/paths.py`: both apps read an original from the store first and fall back to their own copy.
  Bench adds `store()`, `store_version()` and `local_original()`, and it no longer creates `originals/`
  when the store is set up. Label still creates `data/originals/`, because Sync puts audio there.
- `core/settings.py`: both have `dataset_store` (`DATASET_STORE`). Label keeps `CONTRACT_VERSION` here,
  and Bench keeps it in `concord_bench/__init__.py`.
- `core/models.py`: both datasets have `catalog_dataset_id` and `catalog_version`, from migration
  `label_0007` here and `bench_0009` in Bench. Label's `reference_export` also has `catalog_dataset_id`,
  and it keeps its `push` rows only as history.
- `datasets/ingest.py`: both send each original to the store and hold it. Label uploads and holds each
  file in the ingest loop. Bench uploads in `_process_audio` and holds the dataset's audio at the end.
  Bench also refuses a dataset without references.
- `datasets/`: Bench alone has `catalog.py` (import and publish), `store.py` (`just move-originals`) and
  `refpackage.py`. Label's versions of these live in `label/publish.py`, `label/from_catalog.py` and
  `move_originals.py`.
- `platform/`: each app wraps aicore's `DatasetCatalog` with its own app name, Bench in
  `platform/dataset_catalog.py` (`concord-bench`) and Label in `platform/datasets.py` (`concord-label`).
- `runs/jobs.py`: Label runs publishes as `reference` jobs and has the `catalog-dataset` job, and it
  builds reference packages for download only. Bench's jobs didn't change for the Dataset Catalog.
- Frontend `lib/api.ts`: each app has its own Dataset Catalog types, and Label's reference preview lost
  the push fields. Bench's `lib/peer.ts`, which Label doesn't have, links to Label by catalog dataset id.
- The Models and Jobs pages already differed, and both apps changed them only to say "Model Catalog".

Bench also owns `fixtures/generated` and `fixtures/recorded`. When Bench makes them again (`just fixtures`
and `just record`), copy both folders into this repository. `just e2e-handoff` tests the round trip
between the two apps through the Dataset Catalog, with the catalog service and both apps' Docker images on
one network. It needs the catalog image (`aiplatform-catalog:dataset-catalog`, built from the platform's
`catalog/Dockerfile`) and `PLATFORM_DIR`, and `HANDOFF_BENCH_IMAGE` and `HANDOFF_LABEL_IMAGE` pick the app
images.

## Writing style for docs

Write the in-app Docs page (`frontend/src/features/docs/DocsPage.tsx`), README.md and this file in a
plain style. Use simple words and complete sentences, and keep each sentence to one or two clauses. Use
sentence case in headings, straight quotes, and no em dashes, en dashes or middle dots in prose. Write
number ranges with "to", e.g., "1 to 8". Quote a label from the UI exactly as the app shows it, even
when the label contains a middle dot, e.g., "New · manual".

## Commits

Each commit message has a short subject line that says what changed, and a body that explains what
changed and why. Commit one feature at a time where the files allow it.

## Recent changes

These changes came from the updated design canvas and from requests that followed it:

- Offline kits moved to the Model Catalog. Migration 0005 dropped the `offline_export` table, and
  `CONTRACTS.md` §6 and `contracts/samples/kit` were removed here and in Concord Bench.
- Annotate gained several timeline features:
  - You can edit segments on the speaker lanes (move, trim, draw, and add speakers up to 8).
  - You can zoom the timeline down to a one-second window, with an overview strip for panning.
  - You can drag across the waveform to select and play any stretch of the file.
- Space now pauses playback in Annotate. `useAudio` used to attach its listeners before the `<audio>`
  element existed, so the app never knew that audio was playing, and it now takes the element through a
  callback ref.
- Lexicons set house spellings for every dataset, with spelling hints in Annotate. They're stored in
  the tables from migration 0006, and they travel in label packages as `lexicons.json`.
- A Docs page sits under Help in the sidebar, and the Models page groups the models into ASR, VAD and
  diarisation, and speech enhancement.
- The Docs page and README.md were rewritten in the plain style above, and README.md now covers only the
  app's features and its known limitations.
- CONTRACTS.md, HANDOFF.md, PLAN.md and SPLIT_PLAN.md moved into `docs/`. AGENTS.md stays at the
  repository root, where agent tools look for it.
- `just check-contracts` no longer compares `docs/CONTRACTS.md` and `contracts/` with the Bench
  repository. It checks Label's samples, and it checks Label's model adapters against the platform's
  contracts instead.
- The Dataset Catalog came in (the platform's DATASET_CATALOG_PLAN.md, milestones D5 and D6). With
  `DATASET_STORE` set, ingest sends originals to the catalog's store and holds them, Publish to Dataset
  Catalog publishes versions, and New from Dataset Catalog makes a dataset from a version. Migration
  `label_0007` added the catalog columns. `just move-originals` moves `data/originals/` into the store.
- Contract version 3 replaced "Send to Bench" with Publish to Dataset Catalog. Without the catalog, the
  panel builds a reference package to download. Earlier sends stay in the history. Flags, "Open in
  Label" and the locked score find a dataset by its `catalog_dataset_id`.
- Annotate follows the owner's earlier app, pseudolabel ([docs/ANNOTATE_PLAN.md](docs/ANNOTATE_PLAN.md),
  phase 1). A transport row above the timeline holds play and pause, the clock, a speed menu, the zoom, and
  Split, Merge and Delete. A plain mouse wheel over the timeline pans it, and the segment under the playhead
  is highlighted on the lanes and in the list.
- Annotate can undo and redo (ANNOTATE_PLAN.md phase 2). Migration `label_0008` added the `segment_edit`
  table, and `POST /label/files/{file_id}/undo` and `/redo` restore the segments with their old ids and
  uids.
- Annotate's keys follow pseudolabel (ANNOTATE_PLAN.md phase 3), and HANDOFF.md §7 now lists them. S splits
  the segment under the playhead, Enter edits the text, Ctrl+Enter confirms, Delete deletes, and Shift+Tab
  goes back. The page takes Tab only on the page itself, the timeline and the segment list.
- Annotate's segment list reads like pseudolabel's transcript (ANNOTATE_PLAN.md phase 4). The rows live in
  `features/label/SegmentRow.tsx`, with the speaker chip's menu, words that seek, and a text area that
  grows. Tab and Shift+Tab play the segment they land on, and the row above the timeline counts the
  segments to review.
- Annotate's speaker lanes gained pseudolabel's gestures (ANNOTATE_PLAN.md phase 5). A drag of an edge that
  segments share moves all of them through `POST /label/segments/boundary`, as one edit, and Alt moves one
  edge alone. A double-click on a lane adds a 2 second segment, a double-click on a lane's name renames the
  speaker (`rename_speaker` in `edit.bulk`), and a wide enough block shows its text.
- Live evaluation shows cpWER beside WER (`cp_card` in `label/live.py`). It scores each ASR model with the
  speakers of the last pseudo-label run's diariser, on each file up to the first segment nobody has reviewed
  (`reviewed_until`). Ctrl+Enter in Annotate goes to
  the next segment that isn't confirmed, auto-accepted ones included, instead of the next one to review.
