// Help: how to go from raw audio to confirmed references. Static text, written plainly; the search hides
// the sections that do not mention every word typed.

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import * as I from "../../components/icons";
import { Chip, Empty, PageHeader } from "../../components/ui";
import type { SegStatus } from "../../lib/api";
import { SHORTCUTS, STATUS_TONE, statusLabel } from "../../lib/label";
import { MAX_SPEAKERS, MIN_LEN, NUDGE, NUDGE_SHIFT } from "../label/laneDrag";

const STEPS: { n: number; to: string; title: string; text: string }[] = [
  { n: 1, to: "/datasets", title: "Upload a dataset", text: "You upload audio, with references if you have them." },
  { n: 2, to: "/label/pseudo", title: "Pseudo-label", text: "A group of models labels the audio and marks where the models disagree." },
  { n: 3, to: "/label/annotate", title: "Annotate", text: "You fix and confirm the text, the speakers and the timing." },
  { n: 4, to: "/label/live", title: "Evaluate", text: "You score the models against the segments you confirmed." },
  { n: 5, to: "#bench", title: "Publish", text: "You publish the confirmed segments as a version in the Dataset Catalog, where Bench imports them." },
];

const STATUSES: { status: SegStatus; created?: boolean; text: string }[] = [
  { status: "pseudo", text: "The models disagree on the segment, so you should check it by ear." },
  { status: "auto", text: "The models agree on the segment, but nobody has confirmed it yet." },
  { status: "pseudo", created: true, text: "Someone drew the segment by hand on a speaker lane, and it needs a transcript." },
  { status: "rejected", text: "The segment isn't speech or isn't usable, so it's left out of scoring and packages." },
  { status: "confirmed", text: "A person checked the segment, so it's used for evaluation and published as a reference." },
];

const kbd = "num text-[11px] border border-line rounded px-1 text-fg bg-track";

interface Section {
  id: string;
  title: string;
  /** A sub-heading of the section before it (contents list indents it). */
  sub?: boolean;
  body: ReactNode;
}

const SECTIONS: Section[] = [
  {
    id: "workflow",
    title: "How labelling works",
    body: (
      <>
        <p className="m-0">
          Concord Label turns audio into confirmed transcripts and speaker turns. Models label the audio first, and then a person checks the
          segments that the models are unsure about and confirms them.
        </p>
        <ol className="m-0 p-0 list-none grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2.5">
          {STEPS.map((s) => (
            <li key={s.n} className="flex flex-col gap-1 p-3 bg-panel border border-track rounded-lg">
              <span className="num text-xs text-subtle">{s.n}</span>
              {s.to.startsWith("#") ? <a href={s.to} className="text-sm font-medium">{s.title}</a> : <Link to={s.to} className="text-sm font-medium">{s.title}</Link>}
              <span className="text-xs text-muted leading-snug">{s.text}</span>
            </li>
          ))}
        </ol>
      </>
    ),
  },
  {
    id: "datasets",
    title: "Datasets",
    body: (
      <>
        <p className="m-0">
          A dataset is a set of audio files that you upload on <Link to="/datasets">Datasets</Link>. If you already have references, upload them
          with the audio, and they count as confirmed straight away. Otherwise the dataset starts unlabelled.
        </p>
        <p className="m-0">
          Uploads run in the background as jobs, and you can follow them on <Link to="/jobs">Jobs</Link>.
        </p>
        <p className="m-0">
          When the Dataset Catalog is set up, the audio you upload goes to the catalog's store, which Concord Bench reads too. <em>New from
          Dataset Catalog</em> on Datasets makes a dataset from a version that someone published. Its references become confirmed segments, and
          a version with audio only becomes a dataset that is ready to pseudo-label.
        </p>
      </>
    ),
  },
  {
    id: "pseudo",
    title: "Pseudo-labelling",
    body: (
      <>
        <p className="m-0">
          To pseudo-label a dataset, you pick the dataset and the models to run. A diarisation model splits the audio into speaker turns, and
          several ASR models transcribe each segment. The models come from the Model Catalog, and you can see them on{" "}
          <Link to="/models">Models</Link>.
        </p>
        <p className="m-0">
          When the models agree closely on a segment, the segment is auto-accepted. When they disagree, the segment needs review. You can follow
          a run on <Link to="/jobs">Jobs</Link> while it works.
        </p>
        <p className="m-0">
          While a run works, the Pseudo-label page shows its stage and about how long it has left. The estimate uses how fast each model ran
          before, and how fast it runs now. It doesn't count time spent waiting for compute.
        </p>
        <p className="m-0">
          To stop a run, use Cancel run on the Pseudo-label page, or Cancel on the job in Jobs. The run stops before its next segment, and
          it writes no new segments. The model outputs from the files it finished are saved, so a retry or a new run doesn't repeat them.
        </p>
        <p className="m-0">
          Sometimes an ASR model fails on one segment. With Skip a segment when an ASR model fails on it, the run goes on, and the other
          models still transcribe that segment. The results list each skipped segment with the error, and a retry tries those segments
          again. If a model fails on more than 10 segments and more than 5% of its segments, the run stops. Without the option, the run
          stops at the first error.
        </p>
        <p className="m-0 flex items-start gap-2 p-3 rounded-md bg-track/60 text-[13px]">
          <span className="text-focus mt-0.5"><I.Info size={14} /></span>
          Auto-accepted segments aren't confirmed, so a new pseudo-label run may replace them until someone confirms them.
        </p>
      </>
    ),
  },
  {
    id: "annotate",
    title: "Annotating a file",
    body: (
      <>
        <p className="m-0">
          <Link to="/label/annotate">Annotate</Link> shows one file at a time, with the waveform, one lane per speaker, and the table of segments.
          When you select a segment, each model's output for it shows beside the table, and <em>Use this text</em> copies one of them into the
          transcript.
        </p>
        <p className="m-0">
          To check a segment, edit the transcript if it needs it, check the speaker, and then confirm the segment. <em>Confirm all
          high-confidence</em> confirms every auto-accepted segment in the file at once.
        </p>
        <p className="m-0">
          You can review a file from the keyboard. Tab goes to the next segment to review, which is a segment with low confidence or a spelling
          hint, and it plays the segment. The row above the timeline counts the segments left to review. Enter opens the text in the editor, and
          Enter again saves it. You can also double-click the text to edit it. When a segment has word timings, a click on a word moves the
          playhead to it, and the word that is playing is highlighted. Ctrl+Enter confirms the segment and goes to the next one to review.
          Space plays or pauses from the playhead, Shift+Space plays the selected segment, and the arrow keys move the playhead. The table
          under <em>Keyboard shortcuts</em> lists every key.
        </p>
        <p className="m-0">
          <em>Undo</em> and <em>Redo</em> above the timeline take back any change to the file's segments, one change at a time, and their
          tooltips name the change. Label keeps the last 200 changes for each file, and it keeps them when you leave the page. A pseudo-label run
          or an imported package clears the history of the files it changes.
        </p>
        <p className="m-0">
          The row above the waveform holds the player. It plays the file from the playhead, shows the time, and sets the speed from 0.5× to 2×.
          Label remembers the speed in your browser for the next file. While the file plays, the segment under the playhead has a blue outline
          on the speaker lanes and a blue edge in the table, and the table scrolls to keep it in view.
        </p>
        <p className="m-0">
          To listen to any stretch of the file, including a stretch that crosses speakers, drag across the waveform, and the stretch plays at
          once. Shift+Space plays the stretch again, and Escape or a click on the waveform clears it. When you play a segment with its play
          button, a click anywhere else on the timeline stops it there.
        </p>
      </>
    ),
  },
  {
    id: "speakers",
    title: "Editing speakers and segments",
    sub: true,
    body: (
      <>
        <p className="m-0">You edit segments on the speaker lanes under the waveform, in these ways:</p>
        <ul className="m-0 pl-5 flex flex-col gap-1.5">
          <li>To move a segment, drag it sideways. To give it to another speaker, drag it onto that speaker's lane, and press Escape while you drag to cancel.</li>
          <li>To trim a segment, drag the grip at either end. When a grip has the focus, the arrow keys move it by {NUDGE} seconds, or by {NUDGE_SHIFT} seconds with Shift.</li>
          <li>To add a segment, drag across empty space in a lane. Label ignores a segment shorter than {MIN_LEN} seconds, and <em>Segment at playhead</em> adds a one-second segment where the playhead is.</li>
          <li>To add a speaker that the diarisation model missed, use <em>Add speaker</em>. A file can have up to {MAX_SPEAKERS} speakers.</li>
        </ul>
        <p className="m-0">
          To change a segment's speaker, click the speaker chip in the table and pick a speaker from the menu, or press a number from 1 to 8.
          The menu can also take the speaker away, or add a new speaker. To split the
          segment under the playhead, press S, and to merge segments, press M. Delete deletes the selected segment, and Ctrl+Z undoes it.
        </p>
        <p className="m-0">
          For fine edits, you can zoom in on the timeline, down to a one-second window. Use the zoom buttons or the <kbd className={kbd}>=</kbd> and{" "}
          <kbd className={kbd}>-</kbd> keys, or hold Ctrl (⌘ on a Mac) and scroll to zoom where the pointer is. To move along the file, scroll with the
          pointer over the timeline, or drag the window on the overview strip, and press <kbd className={kbd}>0</kbd> to see the whole file again. The view follows
          playback and the segment you select.
        </p>
      </>
    ),
  },
  {
    id: "shortcuts",
    title: "Keyboard shortcuts",
    sub: true,
    body: (
      <div className="overflow-x-auto">
        <table className="w-full max-w-xl border-collapse text-[13px]">
          <caption className="sr-only">Annotate keyboard shortcuts</caption>
          <thead>
            <tr className="text-left text-[11px] text-muted uppercase tracking-wider">
              <th scope="col" className="font-medium px-3 py-2 border-b border-line">Key</th>
              <th scope="col" className="font-medium px-3 py-2 border-b border-line">What it does</th>
            </tr>
          </thead>
          <tbody>
            {SHORTCUTS.map((k) => (
              <tr key={k.key}>
                <td className="px-3 py-2 border-b border-track whitespace-nowrap">
                  <kbd className="num text-[11px] border border-line rounded px-1.5 py-px text-fg bg-track">{k.key}</kbd>
                </td>
                <td className="px-3 py-2 border-b border-track">{k.does}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
  },
  {
    id: "lexicons",
    title: "Lexicons",
    body: (
      <>
        <p className="m-0">
          A lexicon sets one house spelling for a word that people write in more than one way, e.g., <span className="num text-fg">lah</span>{" "}
          instead of <span className="num text-fg">la</span>. Inconsistent spellings in references show up in Bench as substitution errors that the
          model never made.
        </p>
        <p className="m-0">
          In Annotate, Label underlines a variant <span className="text-fg underline decoration-wavy decoration-warn underline-offset-[3px]">like
          this</span>. Click it to use the house spelling or to keep what you wrote, and Label never changes the text unless you choose to.{" "}
          <kbd className={kbd}>Alt+Enter</kbd> uses the house spelling for the selected segment, and Tab stops at spelling hints as well as at
          low-confidence segments.
        </p>
        <p className="m-0">
          You manage lexicons on <Link to="/lexicons">Lexicons</Link>, and every lexicon applies to every dataset. Your edits stay a draft until you
          publish a new version, and Annotate keeps using the published version until then. You can't publish a version while one variant would get
          two different house spellings. Published lexicons also travel in Sync packages.
        </p>
      </>
    ),
  },
  {
    id: "eval",
    title: "Live evaluation",
    body: (
      <>
        <p className="m-0">
          <Link to="/label/live">Live evaluation</Link> scores the models against confirmed segments only. It uses model outputs that are already
          saved, so it never runs a model. It shows these scores:
        </p>
        <ul className="m-0 pl-5 flex flex-col gap-1.5">
          <li>WER (word error rate) compares each model's transcripts with the confirmed ones, after the selected normaliser removes differences such as case and punctuation.</li>
          <li>DER (diarisation error rate) and JER (Jaccard error rate) measure the speaker turns on fully reviewed files. They use a 0.25-second collar, which ignores small timing differences at each segment edge.</li>
          <li>The standings show a 95% interval for each model, computed by resampling the segments many times (bootstrapping).</li>
        </ul>
      </>
    ),
  },
  {
    id: "bench",
    title: "Publishing references",
    body: (
      <>
        <p className="m-0">
          On a dataset's page or on Live evaluation, <em>Publish to Dataset Catalog</em> publishes the confirmed segments as a new version of
          the dataset in the Dataset Catalog. Concord Bench imports versions from there. The first publish makes the dataset in the catalog,
          and each later publish makes the next version. The history on the dataset's page links to each version in the catalog. If someone
          published a newer version after the one your dataset is based on, the panel says who and when, and it asks before you publish anyway.
        </p>
        <p className="m-0">
          Without the Dataset Catalog, the same panel is <em>Download references</em>, which builds the confirmed segments into a reference
          package zip. The package can also include the audio and model outputs, and each package gets a new version number. Label no longer
          sends datasets to Bench directly, so set up the Dataset Catalog to get them there. Scores and flags from Bench come back only for a
          dataset you published to the catalog or made from it.
        </p>
        <p className="m-0">
          Bench's flags, the locked score on Live evaluation and <em>Open in Label</em> find your dataset by the catalog dataset it belongs to.
          A file on a version's page in the Dataset Catalog also has <em>Open in Label</em>. If no dataset here was made from that catalog
          dataset, Label points you to <em>New from Dataset Catalog</em>.
        </p>
        <p className="m-0">
          Either way, the references hold the speakers and the text in SegLST, JSONL or RTTM format, depending on what the segments contain.
          The metadata that came with a file's annotations, e.g., its condition or a NeMo manifest's speaker attributes, goes along as file
          labels, and Bench breaks its scores down by them. Only metadata with the same value on every annotation of the file counts as the
          file's.
        </p>
      </>
    ),
  },
  {
    id: "sync",
    title: "Moving work between installs",
    body: (
      <>
        <p className="m-0">
          <Link to="/label/sync">Sync</Link> exports your labelling progress as a package, and it imports a package from another install. Files
          are matched by a fingerprint of their audio (SHA-256).
        </p>
        <p className="m-0">
          Label compares an import with your data first, and nothing changes until you apply it. Pseudo-labels never overwrite reviewed work, and
          a segment that both sides reviewed differently becomes a conflict for you to resolve.
        </p>
      </>
    ),
  },
  {
    id: "status",
    title: "Segment statuses",
    body: (
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2.5 items-baseline">
        {STATUSES.map((s) => (
          <div key={statusLabel({ status: s.status, created: !!s.created })} className="contents">
            <dt><Chip tone={STATUS_TONE[s.status]}>{statusLabel({ status: s.status, created: !!s.created })}</Chip></dt>
            <dd className="m-0">{s.text}</dd>
          </div>
        ))}
      </dl>
    ),
  },
];

export function DocsPage() {
  const [q, setQ] = useState("");
  const refs = useRef<Record<string, HTMLElement | null>>({});
  const [texts, setTexts] = useState<Record<string, string>>({});
  // The sections are static: read their text once, after the first render.
  useLayoutEffect(() => {
    setTexts(Object.fromEntries(SECTIONS.map((s) => [s.id, (refs.current[s.id]?.textContent ?? "").toLowerCase()])));
  }, []);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (id: string) => !words.length || words.every((w) => (texts[id] ?? "").includes(w));
  const visible = SECTIONS.filter((s) => shown(s.id));

  return (
    <>
      <PageHeader
        crumbs="Help"
        title="Docs"
        subtitle="How to go from raw audio to reviewed references that you can score and publish for Concord Bench."
        actions={
          <label className="relative flex items-center">
            <span className="absolute left-2.5 text-subtle"><I.Search size={14} /></span>
            <span className="sr-only">Search the docs</span>
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the docs" className="w-60 min-h-10 bg-canvas border border-line rounded-md pl-8 pr-2 text-[13px] text-fg" />
          </label>
        }
      />
      <div className="flex flex-col lg:flex-row gap-8 items-start">
        <nav aria-label="On this page" className="lg:sticky lg:top-7 flex flex-col gap-0.5 text-[13px] lg:w-52 shrink-0">
          <span className="text-[11px] text-muted uppercase tracking-[0.06em] px-2.5 pb-1.5">On this page</span>
          {visible.map((s) => (
            <a key={s.id} href={`#${s.id}`} className={`min-h-8 flex items-center rounded-md no-underline !text-muted hover:!text-fg hover:bg-track ${s.sub ? "pl-5" : "px-2.5"}`}>
              {s.title}
            </a>
          ))}
        </nav>
        <article className="flex-1 min-w-0 max-w-[820px] flex flex-col gap-10 text-sm leading-relaxed text-slate-300">
          {words.length > 0 && visible.length === 0 && <Empty>No section mentions "{q.trim()}".</Empty>}
          {SECTIONS.map((s) => (
            <section key={s.id} id={s.id} aria-labelledby={`h-${s.id}`} hidden={!shown(s.id)} ref={(el) => { refs.current[s.id] = el; }} className={`flex flex-col gap-3.5 scroll-mt-6 ${s.sub ? "-mt-5" : ""}`}>
              {s.sub ? (
                <h3 id={`h-${s.id}`} className="m-0 text-[15px] font-semibold text-fg">{s.title}</h3>
              ) : (
                <h2 id={`h-${s.id}`} className="m-0 text-lg font-semibold text-fg">{s.title}</h2>
              )}
              {s.body}
            </section>
          ))}
        </article>
      </div>
    </>
  );
}
