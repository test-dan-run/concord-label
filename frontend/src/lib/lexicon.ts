// Spelling hints from the published lexicons, matched exactly as backend label/lexicon.py does:
// case-insensitive whole words (an apostrophe counts as part of a word), any run of spaces between the
// words of a term, the longest term first; where two lexicons flag one variant, the older lexicon wins.

export interface LexEntry {
  word: string;
  variants: string[];
}

export interface ActiveLexicon {
  id: number;
  name: string;
  version: number;
  entries: LexEntry[];
}

export interface Hint {
  start: number;
  end: number;
  /** The variant as written in the text. */
  found: string;
  /** The house spelling, in the case of `found`. */
  word: string;
  lexiconId: number;
  lexicon: string;
}

export const norm = (t: string): string => t.toLowerCase().split(/\s+/).filter(Boolean).join(" ");

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const WORD = "[\\p{L}\\p{N}_']";

/** The house spelling in the case of the text it replaces: `La` -> `Lah`, `LA` -> `LAH`. */
export function withCase(found: string, word: string): string {
  if (found.length > 1 && found === found.toUpperCase() && found !== found.toLowerCase()) return word.toUpperCase();
  if (found[0] && found[0] !== found[0].toLowerCase()) return word[0].toUpperCase() + word.slice(1);
  return word;
}

export class Matcher {
  private rules = new Map<string, { word: string; lexiconId: number; lexicon: string }>();
  private regex: RegExp | null = null;

  constructor(lexicons: ActiveLexicon[]) {
    for (const lx of [...lexicons].sort((a, b) => a.id - b.id)) {
      for (const e of lx.entries) {
        for (const v of e.variants) {
          const k = norm(v);
          if (k && !this.rules.has(k)) this.rules.set(k, { word: e.word, lexiconId: lx.id, lexicon: lx.name });
        }
      }
    }
    const terms = [...this.rules.keys()].sort((a, b) => b.length - a.length || (a < b ? -1 : 1));
    if (terms.length) {
      const alt = terms.map((t) => t.split(" ").map(escape).join("\\s+")).join("|");
      this.regex = new RegExp(`(?<!${WORD})(?:${alt})(?!${WORD})`, "giu");
    }
  }

  get size(): number {
    return this.rules.size;
  }

  /** Variants in `text`, skipping the ones the annotator kept for this segment. */
  hints(text: string | null | undefined, kept: string[] = []): Hint[] {
    if (!text || !this.regex) return [];
    const skip = new Set(kept.map(norm));
    const out: Hint[] = [];
    for (const m of text.matchAll(this.regex)) {
      const key = norm(m[0]);
      const r = this.rules.get(key);
      if (!r || skip.has(key)) continue;
      out.push({ start: m.index!, end: m.index! + m[0].length, found: m[0], word: withCase(m[0], r.word), lexiconId: r.lexiconId, lexicon: r.lexicon });
    }
    return out;
  }
}

/** `text` with one hint replaced by its house spelling. */
export function houseSpelled(text: string, h: Hint): string {
  return text.slice(0, h.start) + h.word + text.slice(h.end);
}
