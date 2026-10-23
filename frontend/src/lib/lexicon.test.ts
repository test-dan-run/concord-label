import { describe, expect, it } from "vitest";
import { houseSpelled, Matcher, norm, withCase } from "./lexicon";

// The same cases as tests/test_lexicons.py::test_hints_are_whole_words_case_kept_longest_first.
const m = new Matcher([{ id: 1, name: "L", version: 1, entries: [{ word: "lah", variants: ["la"] }, { word: "walao", variants: ["wah lau", "wah"] }] }]);
const text = "Okay La, no problem. LA? la's lala wah  lau, wah.";

describe("lexicon matcher", () => {
  it("finds whole words, keeps case, prefers the longest term", () => {
    const hints = m.hints(text);
    expect(hints.map((h) => [h.found, h.word])).toEqual([["La", "Lah"], ["LA", "LAH"], ["wah  lau", "walao"], ["wah", "walao"]]);
    expect(text.slice(hints[0].start, hints[0].end)).toBe("La");
  });

  it("skips spellings kept for the segment", () => {
    expect(m.hints(text, ["LA"]).map((h) => h.found)).toEqual(["wah  lau", "wah"]);
    expect(m.hints(null)).toEqual([]);
    expect(new Matcher([]).hints("la")).toEqual([]);
  });

  it("matches letters outside ASCII as part of a word", () => {
    expect(m.hints("laé la")).toHaveLength(1);
  });

  it("lets the older lexicon win a variant two lexicons flag", () => {
    const two = new Matcher([
      { id: 2, name: "B", version: 1, entries: [{ word: "lar", variants: ["la"] }] },
      { id: 1, name: "A", version: 3, entries: [{ word: "lah", variants: ["la"] }] },
    ]);
    expect(two.hints("la")[0]).toMatchObject({ word: "lah", lexicon: "A" });
  });

  it("replaces a hint with the house spelling", () => {
    const h = m.hints("Okay la, fine.")[0];
    expect(houseSpelled("Okay la, fine.", h)).toBe("Okay lah, fine.");
    expect(withCase("Wah lau", "walao")).toBe("Walao");
    expect(norm("  Wah   LAU ")).toBe("wah lau");
  });
});
