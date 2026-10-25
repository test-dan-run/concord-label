"""cpWER (metrics/cpwer.py, a copy of Concord Bench's): speaker permutation, and wrong-speaker words counting twice."""

from __future__ import annotations

from concord_label.metrics import cpwer
from concord_label.metrics.cpwer import SpkSegment

REF = [SpkSegment(0, 1, "a", "hello there"), SpkSegment(1, 2, "b", "good morning"), SpkSegment(2, 3, "a", "how are you")]


def test_the_best_speaker_permutation_scores_zero():
    hyp = [SpkSegment(0, 1, "spk1", "hello there"), SpkSegment(1, 2, "spk0", "good morning"),
           SpkSegment(2, 3, "spk1", "how are you")]
    r = cpwer.cpwer(REF, hyp)
    assert r["cpwer"] == 0 and r["n"] == 7 and sorted(r["assignment"]) == [["a", "spk1"], ["b", "spk0"]]


def test_a_word_given_to_the_wrong_speaker_counts_twice():
    ref = [SpkSegment(0, 2, "a", "one two three four"), SpkSegment(2, 3, "b", "five six")]
    hyp = [SpkSegment(0, 1.5, "x", "one two three"), SpkSegment(1.5, 3, "y", "four five six")]
    r = cpwer.cpwer(ref, hyp)
    # "four" is missing from a's words and extra in b's: one deletion and one insertion
    assert (r["errors"], r["d"], r["i"], r["n"]) == (2, 1, 1, 6)
    assert cpwer.aggregate([cpwer.public(r), cpwer.public(cpwer.cpwer(REF, REF))]) == {
        "errors": 2, "n": 13, "s": 0, "d": 1, "i": 1, "cpwer": 2 / 13, "files": 2}
