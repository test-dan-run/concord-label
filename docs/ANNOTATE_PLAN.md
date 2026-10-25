# Plan for a friendlier Annotate page

This plan brings the annotating interactions of the owner's earlier app, pseudolabel, into Annotate. The
owner found Annotate hard to use, and pseudolabel's waveform player, keys and transcript list were easier.
The plan was written on 2026-10-25, and all five phases were built the same day, one commit each.

pseudolabel lives at `github.com/test-dan-run/pseudolabel`. The repository is private, so clone it over
SSH. Its annotation UI is in `services/frontend/src`, and the files this plan draws on are `App.tsx` (the
keys), `player.ts` (playback, zoom and scrolling), `components/Timeline.tsx` (the transport row),
`components/SpeakerLane.tsx` and `components/TranscriptPane.tsx`.

## Decisions

The owner made these decisions on 2026-10-25. Where they differ from HANDOFF.md, they replace it, and
phase 3 updates HANDOFF.md to match.

- The keys follow pseudolabel, even where HANDOFF §7 says otherwise. S splits instead of V, and Enter
  edits the text instead of confirming.
- Space plays or pauses from the playhead, and Shift+Space plays the selected segment.
- A plain mouse wheel over the waveform or the speaker lanes pans the timeline. Ctrl with the wheel zooms.
- Undo and redo are in scope. They need a new table and a migration.
- Word alternatives are out of scope. pseudolabel offers a numbered menu of each word's alternatives
  from the models' votes, and Label won't have it.
- pseudolabel's empty box for text the models disagree on is out of scope. In Label a segment below the
  low threshold gets the "Rejected" status, and that meaning stays.

One choice is still open. With Enter now editing, confirming a segment needs a new key, and the plan
uses Ctrl+Enter. The owner can pick another key before phase 3.

## Keys

Annotate's keys after phase 3 are listed below. `SHORTCUTS` in `frontend/src/lib/label.ts` stays the only
list, so the strip under the segments and the Docs page read the new keys from there.

| Key | What it does |
| --- | --- |
| Space | Plays or pauses from the playhead. |
| Shift+Space | Plays the stretch selected on the waveform, or else the selected segment. |
| ← and → | Moves the playhead 2 seconds back or forward. |
| ↑ and ↓ | Selects the previous or next segment, and moves the playhead to its start. |
| Tab and Shift+Tab | Goes to the next or previous segment to review, and plays it. |
| Enter | Starts editing the selected segment's text. |
| Ctrl+Enter | Confirms the selected segments and goes to the next segment to review. |
| S | Splits the segment under the playhead at the playhead. |
| M | Merges the selected segments, or the selected segment with the next one. |
| Delete | Deletes the selected segment. |
| Ctrl+Z | Undoes the last change to the file. |
| Ctrl+Shift+Z and Ctrl+Y | Redo the last change that was undone. |
| Esc | Closes the open menu, then the editor, then clears the selected rows, then the waveform selection. |
| 1 to 8 | Gives the selected segments to that speaker. This key doesn't change. |
| Alt+Enter | Uses the house spelling for the first spelling hint. This key doesn't change. |
| = and - | Zooms in or out around the playhead. These keys don't change. |
| 0 | Shows the whole file. This key doesn't change. |

Inside the text editor, Enter saves and Esc cancels. Tab saves and goes to the next segment to review, and
Ctrl+Enter saves and confirms. The page ignores the other keys while you type in a text field, as it
does now.

A "segment to review" means the same thing as the current Tab key. It is a segment nobody has reviewed
whose confidence is below the high threshold, or a segment with a spelling hint (`nextLowConfidence` in
`lib/label.ts`).

## What stays the same

HANDOFF.md still asks for the segment grid with checkboxes, shift-click ranges and the bulk bar, so those
stay. The side panel with the model outputs, Flag and Live evaluation also stays. Lane editing, the zoom,
the overview strip and the waveform selection all stay, and the phases below add to them.

These pseudolabel features are not part of the plan:

- The settings drawer and the container queue. Label sets models up on the Pseudo-label page.
- wavesurfer.js. Label's `components/Timeline.tsx` already draws the waveform from peaks the server
  computes.
- The warning before you close the tab. Label saves every edit to the server at once.
- Remembering the last file in the browser. Label keeps the file in the URL.

## Phase 1: the player and the waveform

Phase 1 changes only the frontend. It makes playback easier to control and to follow.

The first change is a transport row. Annotate gets its own `features/label/Transport.tsx`, which renders
the `<audio>` element and replaces `<AudioPlayer>` on this page. The row holds these controls:

- On the left are play and pause, the clock as "1:23 / 4:56", and a speed menu with 0.5×, 0.75×, 1×,
  1.25×, 1.5× and 2×. The browser keeps the speed in `localStorage`, so it carries over to the next
  file. The code wraps each read and write in try/catch.
- In the middle are the waveform selection (`RangeBar`) and the zoom controls (`ZoomBar`).
- On the right are Split, Merge and Delete, each with its key in the tooltip. Split and Delete leave the
  side panel.

The second change is to `useAudio` in `components/AudioPlayer.tsx`. It gains `skip(seconds)` for the
arrow keys. Moreover, a seek now ends the playback of a span, so a segment you started with ▶ doesn't
keep playing after you click somewhere else. `AudioPlayer.tsx` is shared with Bench, so AGENTS.md gets
both changes in its list of Label changes that Bench doesn't have.

The third change is to scrolling. `useWheelZoom` in `features/label/TimelineZoom.tsx` pans the timeline
on a plain vertical wheel, instead of passing it on to the page. Ctrl with the wheel still zooms at the
pointer. The page still scrolls when the pointer is anywhere else.

The fourth change is the segment under the playhead. Annotate works out which segment is playing, which
is the segment that holds the playhead, leaving out rejected segments. It highlights that segment on the
lanes and in the list, apart from the selected one. During playback the list scrolls just enough to keep
that row in view (`scrollIntoView` with `block: "nearest"`).

The last change is small. Space's keyup event no longer reaches a focused button, so pressing Space
doesn't also click whatever button you clicked last.

Tests for phase 1:

- Vitest covers the wheel panning and the function that finds the playing segment.
- Playwright checks the speed menu, a wheel pan, and that a click on the waveform ends a segment's
  playback.

## Phase 2: undo and redo

Phase 2 adds a history of edits on the server, so Ctrl+Z and Ctrl+Shift+Z can undo any change to a file's
segments. The history lives on the server because Label saves every edit at once, and other installs can
receive the result through label packages.

### The table

Migration `label_0008` adds a `segment_edit` table. Each row is one edit to one file.

| Column | Contents |
| --- | --- |
| `id` | The primary key. Edits run in id order. |
| `audio_file_id` | The file. Its foreign key deletes the history with the file (`ondelete="CASCADE"`). |
| `action` | A short name for the edit, e.g., "merge" or "split", which the tooltips show. |
| `before` | JSON. Every column of each segment the edit changed or removed, as it was before. |
| `after` | JSON. Every column of each segment the edit changed or added, as it was after. |
| `undone` | True once the edit is undone. |
| `created_at` | The time of the edit. |

The ORM model goes in `core/models.py`. `tests/test_db_owner.py` pins the revisions up to `label_0008`,
and `next_revision_id()` then returns `label_0009`. `tests/test_migrations.py` checks the new table
against the model.

### Recording edits

A new module, `label/history.py`, records each edit. It has `snapshot(segments)`, `record(session, file,
action, before, after)`, `undo(session, file)` and `redo(session, file)`. Every edit that a person makes
calls `record` in the same transaction as the edit. The edits are:

- `edit.update` for a PATCH, and also a drag on the lanes;
- `edit.bulk`, `edit.merge`, `edit.split`, `edit.create` and `edit.confirm_auto`;
- the delete route in `api/label.py`.

A new edit deletes the file's undone edits, so redo only works straight after an undo. The table keeps
the last 200 edits for each file and deletes older ones.

### Undo and redo

`POST /label/files/{file_id}/undo` takes the newest edit that isn't undone. First, it checks that each
segment in `after` still exists with the same `updated_at`. Second, it deletes the segments in `after`.
Third, it writes back the segments in `before` with their old `id` and `uid`, and it marks the edit as
undone. `POST /label/files/{file_id}/redo` does the same with `before` and `after` the other way round.

Both routes return the action name and the ids of the segments they restored, so the page can select the
first of them and show "Undid merge". `AnnotateView` gains `history: { undo: string | null; redo: string |
null }`, with the action name of the next undo and redo, so the buttons can be disabled and the tooltips
can name the edit.

A restored segment gets a new `updated_at`, while its other columns come back from the snapshot. A label
package export of changes since the last export then still includes the segment.

Something other than a person can change a file's segments, e.g., a pseudo-label run in `label/pseudo.py`
or a package import in `label/sync.py`. When that happens, the code deletes the file's history, because
the snapshots no longer match. If the check in the first step fails anyway, the route answers 409 with
"This file changed since that edit, so it can't be undone", and it deletes the file's history.

No other table points at `segment.id`, so restoring a segment with its old id breaks no link. The
segment's `uid` also comes back, so other installs see the same segment in label packages.

### The page

Annotate gets Undo and Redo buttons in the row above the timeline, with the edit's name in each tooltip.
The phase also binds Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y. These keys work outside text fields only, so the
browser's own undo still works while you type.

Tests for phase 2:

- `tests/test_label.py` undoes and redoes each kind of edit: a text change, a bulk confirm, a merge, a
  split, a create, a delete, "Confirm all high-confidence" and a lane drag.
- `tests/test_label.py` also checks that a new edit clears redo, that the table keeps 200 edits, and that
  a pseudo-label run clears the history and turns a later undo into a 409.
- Playwright merges two segments, presses Ctrl+Z, and checks that both segments are back.

## Phase 3: the keys

Phase 3 puts the key table above into `Annotate.tsx` and into `SHORTCUTS`. The changes from today are:

- Space plays or pauses from the playhead. Shift+Space plays the waveform selection or the segment.
- ← and → skip 2 seconds, and ↑ and ↓ also move the playhead to the segment's start.
- Shift+Tab goes backwards, and Tab and Shift+Tab play the segment they land on (phase 4).
- S splits and V no longer does anything. M is unchanged.
- Enter edits, and Ctrl+Enter confirms and goes to the next segment to review.
- Delete deletes the selected segment. Phase 2 comes first, so a mistaken delete can be undone.
- Esc works through its four steps, from the open menu to the waveform selection.

The page used to take Tab wherever the focus was. Now it takes Tab only when the focus is on the page
itself, on the timeline, or in the segment list. Tab keeps its usual meaning in the header, the file
picker and the bulk bar, so you can still reach those controls from the keyboard.

The phase also updates the documents that describe the keys:

- HANDOFF.md §7 and its Annotate section, which mentions "speaker pill cycling" and "Enter confirm";
- the Annotate section of the Docs page (`features/docs/DocsPage.tsx`) and README.md, in the plain style
  from AGENTS.md;
- the header comment of `Annotate.tsx`, which lists the keys.

Tests for phase 3:

- `frontend/src/components/label.test.tsx` checks that `SHORTCUTS` lists each key once.
- `e2e/label.spec.ts` presses each key and checks the result.

## Phase 4: the segment list and the review flow

Phase 4 makes the segment list easier to read and to move through. The list stays a grid with checkboxes,
because HANDOFF.md asks for multi-select, but each row now reads like pseudolabel's.

### The rows

Each row shows these columns, from left to right:

1. The checkbox, the ▶ button that plays the segment, and the start time. A click on the start time
   moves the playhead to the start of the segment.
2. The speaker chip.
3. The text, which takes the most room and wraps onto more lines.
4. The confidence, the status and the ✓ and ✗ buttons, in a narrow column.

The speaker chip opens a menu instead of cycling through the speakers. The menu lists each speaker with
its colour and its 1 to 8 key, then "No speaker", then "New speaker". "New speaker" adds a speaker with
`nextSpeakerName` and gives it the segment, and it's disabled when the file already has 8 speakers.

The text shows as text until you edit it. A click on the row selects the segment. When the segment has
word timings (`Segment.words`), each word is a button, and a click on a word moves the playhead to it.
While the segment plays, the word under the playhead is highlighted. Segments without word timings show
plain text.

You edit the text by pressing Enter, by double-clicking it, or with the ✎ button that shows on hover. The
editor is a text area that grows with the text instead of scrolling. Enter saves, Esc cancels, and Tab
saves and goes to the next segment to review. Spelling hints keep their wavy underline and their "Use"
and "Keep" choices.

### The review flow

`nextLowConfidence` becomes `nextToReview(segs, fromId, direction, high, also)`, which can also step
backwards. When Tab or Shift+Tab lands on a segment, the page selects it, brings it into view on the
timeline, and plays it from start to end. The row above the timeline shows "12 to review" with ‹ and ›
buttons, and it says "Nothing left to review" when the count is 0.

Tests for phase 4:

- Vitest covers `nextToReview` in both directions, with and without a wrap at the end.
- Playwright edits a text with Enter and with a double-click, assigns a speaker from the chip's menu, and
  presses Tab to check that the next segment plays.

## Phase 5: the speaker lanes

Phase 5 adds pseudolabel's lane gestures to `features/label/SpeakerLanes.tsx`. The drag maths goes in
`laneDrag.ts` and its tests in `laneDrag.test.ts`, as now.

The first change is shared boundaries. Two segments touch when one ends within 20 ms of where the other
starts, on any lane. When you drag the edge where two segments touch, both segments move together. That
keeps a change of speaker in the same place on both lanes. The page saves the drag through a new
`POST /label/segments/boundary` with `{ ids, t }`, so the drag is one edit in the history. Alt with the
drag moves one edge alone.

The second change is adding a segment with a double-click. A double-click on an empty part of a lane adds
a 2 second segment centred on the click, for that lane's speaker. The new segment stops short of the
segments next to it on the same lane. Drawing a segment by dragging still works.

The third change is renaming a speaker. A double-click on a lane's name opens a text field. Enter
renames the speaker on every segment of the file, through a new `rename_speaker` action in
`edit.bulk`, and Esc cancels. When the new name is already another speaker's, the page asks before it
merges the two speakers. A lane you added that has no segments yet is renamed on the page alone.

The last change shows each segment's text inside its block on the lane when the block is wide enough,
cut short with an ellipsis.

Tests for phase 5:

- `laneDrag.test.ts` covers touching edges and the double-click segment next to neighbours.
- `tests/test_label.py` covers the boundary route, `rename_speaker`, and the undo of both.
- Playwright drags a shared edge, renames a lane, and adds a segment with a double-click.

## Where the build differs from the plan

- Undo and redo don't compare `updated_at`. An undo gives each restored segment a new `updated_at`, so that
  check would refuse every redo. They compare every other column instead, and `edited_at` only by whether
  it is set.
- During playback the segment list scrolls itself, instead of calling `scrollIntoView`, so the page doesn't
  jump to the list while you watch the timeline.
- `nextToReview` came in phase 3, because Shift+Tab needs it. Ctrl+Z came into `SHORTCUTS` in phase 2,
  together with the key.
- The arrow keys on a focused grip still move one edge alone, even where two segments touch.
- Ctrl+Enter confirms, and the owner can still pick another key.

## Order of work and checks

The phases run in the order above. Phase 2 comes before phase 3 so that the Delete key and Ctrl+Z arrive
together, and each phase is one commit. Before each commit, run `just test` and `just lint`, run the
Playwright tests (`npm run build`, then `npx playwright test` in `frontend/`), and look at Annotate in a
browser.

Each phase updates the Docs page and README.md for the parts it changes. AGENTS.md gets a line under
"Recent changes", and the last phase adds this plan to the list of documents at the top of AGENTS.md.
