// A row of Annotate's segment list, laid out like pseudolabel's transcript: the checkbox, play and the start
// time (a click there moves the playhead); the speaker chip, which opens a menu of the speakers; the text,
// which wraps and shows as text until you edit it (Enter, a double-click or the pen); and a narrow column with
// the confidence, the status, and confirm and reject. With word timings, each word moves the playhead to
// itself, and the word under the playhead is highlighted while the segment plays. Spelling hints are
// underlined, with a choice to use the house spelling or keep the text.

import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { PlayFrom, type AudioController } from "../../components/AudioPlayer";
import * as I from "../../components/icons";
import { Button, Chip, cx } from "../../components/ui";
import type { Segment } from "../../lib/api";
import { clock } from "../../lib/format";
import { confidenceColor, STATUS_TONE, statusLabel, timedWords, wordAt, type TimedWord } from "../../lib/label";
import type { Hint } from "../../lib/lexicon";
import { MAX_SPEAKERS } from "./laneDrag";

/** The menu open on a row: the speaker chip's, or a spelling hint's (by its index). */
export type RowMenu = "speaker" | number | null;

/** `text` split around its hints: plain strings, and the index of each hint in between. */
function pieces(text: string, hints: Hint[]): (string | number)[] {
  const out: (string | number)[] = [];
  let at = 0;
  hints.forEach((h, k) => {
    if (h.start > at) out.push(text.slice(at, h.start));
    out.push(k);
    at = h.end;
  });
  if (at < text.length) out.push(text.slice(at));
  return out;
}

export function SegmentRow({
  s,
  rowRef,
  current,
  playing,
  checked,
  color,
  colors,
  speakers,
  high,
  low,
  ctl,
  onFocus,
  onCheck,
  onSpeaker,
  onNewSpeaker,
  onText,
  hints,
  onUseHint,
  onKeepHint,
  onConfirm,
  onReject,
  editing,
  onEdit,
  menu,
  onMenu,
  onNext,
  onConfirmNext,
}: {
  s: Segment;
  rowRef: (el: HTMLTableRowElement | null) => void;
  current: boolean;
  /** Under the playhead. */
  playing: boolean;
  checked: boolean;
  color: string;
  colors: Record<string, string>;
  /** The file's speakers, in the order of the 1 to 8 keys. */
  speakers: string[];
  high: number;
  low: number;
  ctl: AudioController;
  onFocus: () => void;
  onCheck: (shift: boolean) => void;
  onSpeaker: (speaker: string | null) => void;
  onNewSpeaker: () => void;
  onText: (t: string) => void;
  hints: Hint[];
  onUseHint: (h: Hint) => void;
  onKeepHint: (h: Hint) => void;
  onConfirm: () => void;
  onReject: () => void;
  /** The text is in the editor. */
  editing: boolean;
  onEdit: (on: boolean) => void;
  menu: RowMenu;
  onMenu: (m: RowMenu) => void;
  /** Tab and Shift+Tab in the editor: save, then go to the next or previous segment to review. */
  onNext: (direction: 1 | -1) => void;
  /** Ctrl+Enter in the editor: save, confirm, and go to the next segment to review. */
  onConfirmNext: () => void;
}) {
  const chip = useRef<HTMLButtonElement>(null);
  const hint = typeof menu === "number" && hints[menu] ? hints[menu] : null;
  return (
    <tr ref={rowRef} onClick={onFocus} aria-current={current || undefined} data-playing={playing || undefined} className={cx("border-b border-track align-top", current ? "bg-active" : playing ? "bg-focus/10" : "hover:bg-track/60", s.status === "rejected" && "opacity-60")}>
      <td className={cx("pl-2 pr-1 py-2", playing && "shadow-[inset_3px_0_0_var(--color-focus)]")}>
        <input
          type="checkbox"
          aria-label={`Select segment at ${clock(s.start)}`}
          checked={checked}
          onChange={() => undefined}
          onClick={(e) => {
            e.stopPropagation();
            onCheck(e.shiftKey);
          }}
          className="mt-1.5"
        />
      </td>
      <td className="px-1 py-1.5">
        <PlayFrom ctl={ctl} t={s.start} until={s.end} label={`Play segment at ${clock(s.start)}`} />
      </td>
      <td className="px-1 py-1.5">
        <button
          type="button"
          title="Move the playhead to the start of the segment"
          onClick={(e) => {
            e.stopPropagation();
            onFocus();
            ctl.seek(s.start);
          }}
          className="min-h-7 px-1 border-0 rounded bg-transparent num text-xs text-muted hover:text-fg hover:bg-track cursor-pointer whitespace-nowrap"
        >
          {clock(s.start)}
        </button>
      </td>
      <td className="px-1 py-1.5">
        <button
          ref={chip}
          type="button"
          aria-haspopup="menu"
          aria-expanded={menu === "speaker"}
          onClick={(e) => {
            e.stopPropagation();
            onFocus();
            onMenu(menu === "speaker" ? null : "speaker");
          }}
          title="Change the speaker (or press 1 to 8)"
          className="inline-flex items-center gap-1.5 mt-0.5 rounded-full border px-2 py-0.5 text-xs cursor-pointer bg-transparent text-fg num whitespace-nowrap"
          style={{ borderColor: color }}
        >
          <span className="size-2 rounded-full" style={{ background: color }} />
          {s.speaker ?? "—"}
          <I.Chevron size={10} open={menu === "speaker"} />
        </button>
        {menu === "speaker" && chip.current && (
          <SpeakerMenu anchor={chip.current} speakers={speakers} colors={colors} current={s.speaker} onPick={onSpeaker} onNew={onNewSpeaker} onClose={() => onMenu(null)} />
        )}
      </td>
      <td className="px-2 py-1.5 w-full">
        {editing ? (
          <Editor s={s} onText={onText} onEdit={onEdit} onNext={onNext} onConfirmNext={onConfirmNext} />
        ) : (
          <div className="flex flex-col gap-1.5">
            <Transcript s={s} hints={hints} open={typeof menu === "number" ? menu : null} playing={playing} ctl={ctl} onFocus={onFocus} onEdit={() => onEdit(true)} onHint={(k) => onMenu(menu === k ? null : k)} />
            {hint && (
              <div role="group" aria-label="Spelling hint" className="flex flex-wrap items-center gap-2 px-2 py-1.5 rounded-md border border-line bg-canvas text-xs text-muted" onClick={(e) => e.stopPropagation()}>
                House spelling <span className="num text-fg">{hint.word}</span>
                <span className="text-subtle">· {hint.lexicon}</span>
                <span className="ml-auto flex gap-1.5">
                  <Button size="sm" variant="primary" onClick={() => onUseHint(hint)}>Use</Button>
                  <Button size="sm" onClick={() => onKeepHint(hint)}>Keep</Button>
                </span>
              </div>
            )}
          </div>
        )}
      </td>
      <td className="px-2 py-1.5">
        <div className="flex flex-col items-end gap-1">
          <span className="flex items-center gap-1.5 whitespace-nowrap">
            {s.flagged && <span className="text-warn" title={s.flag_note ?? "Flagged"}><I.Flag size={12} /></span>}
            <span className="num text-xs font-semibold" style={{ color: confidenceColor(s.confidence, high, low) }}>
              {s.confidence === null ? "—" : s.confidence.toFixed(2)}
            </span>
            <Chip tone={STATUS_TONE[s.status]}>{statusLabel(s)}</Chip>
          </span>
          <span className="flex gap-1">
            <button type="button" aria-label="Confirm" title="Confirm (Ctrl+Enter)" onClick={(e) => { e.stopPropagation(); onConfirm(); }} className="size-7 inline-flex items-center justify-center rounded border border-line bg-transparent text-good cursor-pointer hover:bg-track">
              <I.Check size={12} />
            </button>
            <button type="button" aria-label="Reject" title="Reject" onClick={(e) => { e.stopPropagation(); onReject(); }} className="size-7 inline-flex items-center justify-center rounded border border-line bg-transparent text-bad cursor-pointer hover:bg-track">
              <I.X size={12} />
            </button>
          </span>
        </div>
      </td>
    </tr>
  );
}

/** The text as text: words that move the playhead (with word timings), spelling hints underlined, and the pen. */
function Transcript({
  s,
  hints,
  open,
  playing,
  ctl,
  onFocus,
  onEdit,
  onHint,
}: {
  s: Segment;
  hints: Hint[];
  open: number | null;
  playing: boolean;
  ctl: AudioController;
  onFocus: () => void;
  onEdit: () => void;
  onHint: (k: number) => void;
}) {
  const text = s.text ?? "";
  const words = timedWords(s.text, s.words);
  const now = playing && words ? wordAt(words, ctl.time) : null;
  // A plain stretch of the text starting at `at`: with word timings, each word is a button.
  const plain = (str: string, at: number, key: string): ReactNode => {
    if (!words) return <span key={key}>{str}</span>;
    let o = at;
    return str.split(/(\s+)/).map((part, j) => {
      const here = o;
      o += part.length;
      const k = words.findIndex((w: TimedWord) => w.start === here);
      if (!part.trim() || k < 0) return <span key={`${key}.${j}`}>{part}</span>;
      return (
        <button
          key={`${key}.${j}`}
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onFocus();
            ctl.seek(words[k].t0);
          }}
          className={cx("p-0 border-0 rounded-sm text-fg text-[13px] leading-[inherit] cursor-pointer hover:bg-track", k === now ? "bg-focus/30" : "bg-transparent")}
        >
          {part}
        </button>
      );
    });
  };
  let at = 0;
  return (
    <div
      data-transcript
      onDoubleClick={(e) => {
        e.stopPropagation();
        onEdit();
      }}
      className="group/t px-1.5 py-1 rounded border border-transparent hover:border-line text-fg text-[13px] leading-relaxed whitespace-pre-wrap break-words cursor-text"
    >
      {s.text === null ? (
        <span className="text-subtle">(no transcript)</span>
      ) : (
        pieces(text, hints).map((p, k) => {
          if (typeof p === "string") {
            const node = plain(p, at, String(k));
            at += p.length;
            return node;
          }
          at = hints[p].end;
          return (
            <button
              key={k}
              type="button"
              aria-expanded={open === p}
              aria-label={`${hints[p].found}: house spelling is ${hints[p].word}`}
              onClick={(e) => {
                e.stopPropagation();
                onFocus();
                onHint(p);
              }}
              className="p-0 border-0 bg-transparent text-fg text-[13px] leading-[inherit] cursor-pointer underline decoration-wavy decoration-warn underline-offset-[3px]"
            >
              {hints[p].found}
            </button>
          );
        })
      )}
      <button
        type="button"
        aria-label={`Edit transcript at ${clock(s.start)}`}
        title="Edit (Enter or double-click)"
        onClick={(e) => {
          e.stopPropagation();
          onFocus();
          onEdit();
        }}
        className="ml-1.5 p-0 border-0 bg-transparent text-subtle hover:text-fg cursor-pointer align-middle opacity-0 group-hover/t:opacity-100 focus-visible:opacity-100"
      >
        <I.Pen size={12} />
      </button>
    </div>
  );
}

/** The text editor: a text area that grows with the text. Enter saves, Esc cancels, Tab saves and goes on to
 * the next segment to review, and Ctrl+Enter saves and confirms. Leaving it saves. */
function Editor({ s, onText, onEdit, onNext, onConfirmNext }: { s: Segment; onText: (t: string) => void; onEdit: (on: boolean) => void; onNext: (direction: 1 | -1) => void; onConfirmNext: () => void }) {
  const [draft, setDraft] = useState(s.text ?? "");
  const box = useRef<HTMLTextAreaElement>(null);
  const grow = () => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };
  useLayoutEffect(grow, [draft]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);
  // Set while a key closes the editor, so the blur that follows doesn't save (or cancel) a second time.
  const closing = useRef(false);
  const save = () => {
    if (draft !== (s.text ?? "")) onText(draft);
  };
  const close = (keep: boolean) => {
    if (keep) save();
    closing.current = true;
    box.current?.blur();
    closing.current = false;
    onEdit(false);
  };
  const onKey = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      close(true);
      onConfirmNext();
    } else if (e.key === "Enter" && !e.altKey) {
      e.preventDefault();
      close(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close(false);
    } else if (e.key === "Tab") {
      e.preventDefault();
      close(true);
      onNext(e.shiftKey ? -1 : 1);
    }
  };
  return (
    <textarea
      ref={box}
      rows={1}
      aria-label={`Transcript at ${clock(s.start)}`}
      value={draft}
      placeholder={s.text === null ? "(no transcript)" : ""}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (closing.current) return;
        save();
        onEdit(false);
      }}
      onKeyDown={onKey}
      className="block w-full resize-none overflow-hidden bg-canvas border border-focus rounded px-1.5 py-1 text-fg text-[13px] leading-relaxed outline-none"
    />
  );
}

/** The speaker chip's menu: each speaker with its colour and its 1 to 8 key, then "No speaker" and "New
 * speaker". It closes on a click outside it, and the arrow keys move between its items. */
function SpeakerMenu({
  anchor,
  speakers,
  colors,
  current,
  onPick,
  onNew,
  onClose,
}: {
  anchor: HTMLElement;
  speakers: string[];
  colors: Record<string, string>;
  current: string | null;
  onPick: (speaker: string | null) => void;
  onNew: () => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  // Under the chip, or above it near the bottom of the window; it follows the chip when the list or the page
  // scrolls (the list scrolls by itself during playback).
  useLayoutEffect(() => {
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const h = box.current?.offsetHeight ?? 0;
      setPos({ left: r.left, top: r.bottom + 4 + h > window.innerHeight ? Math.max(4, r.top - 4 - h) : r.bottom + 4 });
    };
    place();
    box.current?.querySelector<HTMLButtonElement>("[role=menuitem]:not(:disabled)")?.focus({ preventScroll: true });
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [anchor]);
  useEffect(() => {
    // a click on the chip toggles the menu itself
    const close = (e: Event) => {
      if (!box.current?.contains(e.target as Node) && !anchor.contains(e.target as Node)) onClose();
    };
    window.addEventListener("pointerdown", close, true);
    return () => window.removeEventListener("pointerdown", close, true);
  }, [anchor, onClose]);
  const onKey = (e: ReactKeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    e.stopPropagation();
    const items = [...(box.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)") ?? [])];
    const k = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(k + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  };
  const item = "flex w-full items-center gap-2 min-h-8 px-3 border-0 bg-transparent text-left text-xs text-fg cursor-pointer hover:bg-track focus-visible:bg-track disabled:text-subtle disabled:cursor-not-allowed";
  return (
    <div
      ref={box}
      role="menu"
      aria-label="Speaker"
      onKeyDown={onKey}
      onClick={(e) => e.stopPropagation()}
      className="fixed z-30 min-w-44 py-1 rounded-md border border-line bg-panel shadow-lg"
      style={pos ? { left: pos.left, top: pos.top } : { visibility: "hidden" }}
    >
      {speakers.map((name, k) => (
        <button key={name} type="button" role="menuitem" aria-current={name === current || undefined} onClick={() => onPick(name)} className={item}>
          <span className="size-2 rounded-full shrink-0" style={{ background: colors[name] }} />
          <span className="num flex-1">{name}</span>
          {name === current && <I.Check size={12} />}
          {k < MAX_SPEAKERS && <kbd className="num text-[10px] border border-line rounded px-1 text-muted bg-track">{k + 1}</kbd>}
        </button>
      ))}
      <div className="my-1 border-t border-track" />
      <button type="button" role="menuitem" onClick={() => onPick(null)} className={item}>
        <span className="size-2 rounded-full shrink-0 bg-subtle" />
        <span className="flex-1">No speaker</span>
      </button>
      <button type="button" role="menuitem" disabled={speakers.length >= MAX_SPEAKERS} title={speakers.length >= MAX_SPEAKERS ? `A file can have up to ${MAX_SPEAKERS} speakers` : undefined} onClick={onNew} className={item}>
        <I.Plus size={12} />
        <span className="flex-1">New speaker</span>
      </button>
    </div>
  );
}
