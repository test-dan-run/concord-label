# Notes for coding agents

Concord Label is a web app for labelling speech audio, with a FastAPI backend in `backend/concord_label`
and a React frontend in `frontend/src`. Read [README.md](README.md) for how the app works, and read
[HANDOFF.md](HANDOFF.md) for the behaviour the owner asked for. The design canvas that HANDOFF.md links
shows every screen, and where the canvas and HANDOFF.md disagree, HANDOFF.md wins.

## Commands

```bash
just setup            # uv sync and npm install
just dev              # API on :8620, the worker, and Vite on :5174
just test             # pytest, then the frontend unit tests (vitest)
just lint             # ruff, import-linter, and tsc
just check-contracts  # contracts/samples, compared with the Concord Bench repository
```

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
- `CONTRACTS.md` and `contracts/` must stay identical to the copies in the Concord Bench repository.
  When you change them, the same change has to land in Bench.
- Some modules are copies of Concord Bench's code, and README.md lists them under "Code copied from
  Concord Bench". When you fix one of them, note the fix for Bench, and keep the adapters' params
  identical in both apps.
- The lexicon matcher exists twice, as `backend/concord_label/label/lexicon.py` and
  `frontend/src/lib/lexicon.ts`. Change both together, and keep `tests/test_lexicons.py` and
  `frontend/src/lib/lexicon.test.ts` on the same cases.
- Annotate's keyboard shortcuts are listed once, in `SHORTCUTS` in `frontend/src/lib/label.ts`. The
  strip under the segment table and the Docs page both read that list, so add a new shortcut there.
- `components/Timeline.tsx` is shared with Bench and draws the waveform and the axis. The speaker lane
  editing in Annotate lives in `features/label/SpeakerLanes.tsx`, the drag maths in `laneDrag.ts`, and
  the zoom maths in `zoom.ts`, and all three are Label's own.
- Offline kits now belong to the platform's model catalog, so don't add offline export back to Label.
- Match the style of the code around your change, including how many comments it has.

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

- Offline kits moved to the model catalog. Migration 0005 dropped the `offline_export` table, and
  `CONTRACTS.md` §6 and `contracts/samples/kit` were removed. `just check-contracts` reports a
  difference until Concord Bench removes them too.
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
- The Docs page and README.md were rewritten in the plain style above.
