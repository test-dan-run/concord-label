// Help: how to go from raw audio to confirmed references. Static text; the search hides the sections
// that do not mention every word typed.

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import * as I from "../../components/icons";
import { Chip, Empty, PageHeader } from "../../components/ui";
import type { SegStatus } from "../../lib/api";
import { SHORTCUTS, STATUS_TONE, statusLabel } from "../../lib/label";
import { MAX_SPEAKERS, MIN_LEN, NUDGE, NUDGE_SHIFT } from "../label/laneDrag";

function B({ children }: { children: ReactNode }) {
  return <strong className="text-fg font-semibold">{children}</strong>;
}

const STEPS: { n: number; to: string; title: string; text: string }[] = [
  { n: 1, to: "/datasets", title: "Upload a dataset", text: "Audio, with references if you have them." },
  { n: 2, to: "/label/pseudo", title: "Pseudo-label", text: "An ensemble labels it and flags disagreements." },
  { n: 3, to: "/label/annotate", title: "Annotate", text: "Fix and confirm text, speakers and timing." },
  { n: 4, to: "/label/live", title: "Evaluate", text: "Score models against what you confirmed." },
  { n: 5, to: "#bench", title: "Send to Bench", text: "Package confirmed segments as a reference set." },
];

const STATUSES: { status: SegStatus; created?: boolean; text: string }[] = [
  { status: "pseudo", text: "The models disagree. Check it by ear." },
  { status: "auto", text: "The models agree. Not yet confirmed by a person." },
  { status: "pseudo", created: true, text: "Drawn by hand on a speaker lane. Needs a transcript." },
  { status: "rejected", text: "Not speech, or not usable. Left out of scoring and packages." },
  { status: "confirmed", text: "Checked by a person. Used for evaluation and sent to Bench." },
];

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
    title: "The workflow",
    body: (
      <>
        <p className="m-0">
          Concord Label turns audio into confirmed transcripts and speaker turns. Models do the first pass; people check what the models are unsure of.
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
          A dataset is a set of audio files. Upload one from <Link to="/datasets">Datasets</Link>. If you already have references, upload them with the
          audio and they count as confirmed straight away; otherwise the dataset starts unlabelled.
        </p>
        <p className="m-0">
          Uploads run in the background as jobs. Follow them on <Link to="/jobs">Jobs</Link>.
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
          Pick a dataset and the models to run. Each segment is transcribed by several ASR models and split into speaker turns by a diarisation
          model. The models available come from the model catalog; see <Link to="/models">Models</Link>.
        </p>
        <p className="m-0">
          Where the models agree closely, the segment is <B>auto-accepted</B>. Where they disagree, it goes to <B>needs review</B>. Runs show up
          on <Link to="/jobs">Jobs</Link> while they work.
        </p>
        <p className="m-0 flex items-start gap-2 p-3 rounded-md bg-track/60 text-[13px]">
          <span className="text-focus mt-0.5"><I.Info size={14} /></span>
          Auto-accepted segments are not confirmed. A new pseudo-label run may replace them until someone confirms them.
        </p>
      </>
    ),
  },
  {
    id: "annotate",
    title: "Annotating",
    body: (
      <>
        <p className="m-0">
          <Link to="/label/annotate">Annotate</Link> shows one file at a time: the waveform, a lane per speaker, and the segment table. Select a
          segment to see each model’s output beside it; use <em>Use this text</em> to copy one into the transcript.
        </p>
        <p className="m-0">
          Edit the transcript, check the speaker, then confirm. <em>Confirm all high-confidence</em> confirms every auto-accepted segment in the
          file at once.
        </p>
      </>
    ),
  },
  {
    id: "speakers",
    title: "Speakers and segments",
    sub: true,
    body: (
      <>
        <p className="m-0">Most of this happens on the speaker lanes under the waveform:</p>
        <ul className="m-0 pl-5 flex flex-col gap-1.5">
          <li><B>Move a segment:</B> drag the bar sideways. Drag it onto another lane to give it to that speaker. Press Escape while dragging to cancel.</li>
          <li><B>Trim a segment:</B> drag the grip at either end. With a grip focused, the arrow keys nudge it by {NUDGE} s, or {NUDGE_SHIFT} s with Shift.</li>
          <li><B>Add a segment:</B> drag across empty space in a lane. Segments shorter than {MIN_LEN} s are ignored. <em>Segment at playhead</em> adds a one-second one where the playhead is.</li>
          <li><B>Add a speaker:</B> use <em>Add speaker</em> when someone speaks who the diarisation model missed, up to {MAX_SPEAKERS} speakers.</li>
          <li><B>Change a speaker:</B> click the speaker chip in the table to cycle through speakers, or press 1–8.</li>
          <li><B>Split or merge:</B> press V to split at the playhead, M to merge.</li>
        </ul>
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
              <th scope="col" className="font-medium px-3 py-2 border-b border-line">Does</th>
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
          A lexicon sets one house spelling for words that get written several ways, such as <span className="num text-fg">lah</span> over{" "}
          <span className="num text-fg">la</span>. Inconsistent spellings in references show up in Bench as substitution errors that the model
          never made.
        </p>
        <p className="m-0">
          In Annotate, a variant is underlined <span className="text-fg underline decoration-wavy decoration-warn underline-offset-[3px]">like this</span>.
          Click it to use the house spelling or keep what you wrote; it is never changed for you. <kbd className="num text-[11px] border border-line rounded px-1 text-fg bg-track">Alt+Enter</kbd> uses
          the house spelling for the selected segment, and Tab stops at hints as well as low-confidence segments.
        </p>
        <p className="m-0">
          Manage lexicons on <Link to="/lexicons">Lexicons</Link>. Every lexicon applies to every dataset. Edits are a draft until you publish a
          new version; Annotate keeps the published version until then. A version cannot be published while one variant would get two house
          spellings. Published lexicons travel in Sync packages.
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
          <Link to="/label/live">Live evaluation</Link> scores models against confirmed segments only, using model outputs already saved. It
          never runs inference.
        </p>
        <ul className="m-0 pl-5 flex flex-col gap-1.5">
          <li><B>WER</B> compares transcripts after the selected normaliser is applied.</li>
          <li><B>DER and JER</B> measure speaker turns, on files that are fully reviewed, with a 0.25 s collar.</li>
          <li><B>Standings</B> show 95% intervals, bootstrapped over segments.</li>
        </ul>
      </>
    ),
  },
  {
    id: "bench",
    title: "Sending to Bench",
    body: (
      <p className="m-0">
        From a dataset’s page or Live evaluation, <em>Send to Bench</em> packages confirmed segments as a reference set for Concord Bench: speaker
        and text in SegLST format, with optional audio and model outputs. Each package is versioned, so later sends don’t overwrite earlier
        ones.
      </p>
    ),
  },
  {
    id: "sync",
    title: "Moving work between installs",
    body: (
      <p className="m-0">
        <Link to="/label/sync">Sync</Link> exports labelling progress as a package and imports one from another install. Files are matched by
        their audio fingerprint. An import is compared first; nothing changes until you apply it, and reviewed work is never overwritten by
        pseudo-labels.
      </p>
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
        subtitle="How to go from raw audio to reviewed references you can score and send to Concord Bench."
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
          {words.length > 0 && visible.length === 0 && <Empty>No section mentions “{q.trim()}”.</Empty>}
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
