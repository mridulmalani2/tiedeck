"""Precision as a gate: how often the tool is wrong on decks with known defects.

PLAN.md §0. The corpus in :mod:`tests.corpus` labels every defect a person would
want reported and every oddity a designer put there on purpose, and this module
holds the tool to two numbers per rule -- what it caught and what it got wrong.

Both are pinned **exactly**, not merely bounded. A ratchet that only fails when
recall falls lets a rule start catching something new without anyone writing
down that it does, and lets a false positive be swapped for a different one
with the count unchanged. So the lists below are the measurement, and changing
what the tool does means changing them, here and in PLAN.md §0.1, in the same
commit. The floor can only rise and the ceiling can only fall: a commit that
moves either the other way has to say why in this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.corpus import CASES, Report, score

#: Defects in the corpus the tool does not report, and why each one is missed.
#:
#: The baseline's one miss -- CO-001 abandoning a metric after its first
#: disagreement, in every period -- was fixed (PLAN.md §0.1). The one here is
#: the cost of ``kind``, predicted before it was measured: a percentage nothing
#: names the kind of is not compared. It is declined out loud, which the
#: ``[declined, and said so]`` pins.
KNOWN_MISSES: frozenset[str] = frozenset(
    {
        "CO-001: heron-kinds: 'Churn of 4.8%' contradicts the table's 3.8% -- no word "
        "says what kind of figure churn is, so PLAN.md §0 predicts this one goes quiet "
        "[declined, and said so]",
    }
)

#: Findings on something labelled intentional, or on nothing labelled at all.
#: Keyed by rule, case and slide; the message is not pinned, so rewording a
#: finding does not count as changing what the tool does.
KNOWN_FALSE_POSITIVES: frozenset[str] = frozenset(
    {
        # A decorative bleed in a house style whose reference deck does not
        # bleed: reported at info, with "Nothing to do unless this was not
        # intended".
        "LO-001 osprey-layout slide 1",
        "LO-001 osprey-layout slide 2",
        "LO-001 osprey-layout slide 8",
        # The same off-grid takeaway box on three slides.
        "LO-003 osprey-layout slide 5",
        "LO-003 osprey-layout slide 6",
        "LO-003 osprey-layout slide 7",
    }
)


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> Report:
    return score(CASES, Path(tmp_path_factory.mktemp("corpus")))


def _missed(report: Report) -> set[str]:
    return {
        f"{rule_id}: {line}"
        for rule_id, score_ in report.scores.items()
        for line in score_.missed
    }


def _wrong(report: Report) -> set[str]:
    return {
        f"{rule_id} {line.split(':', 1)[0]}"
        for rule_id, score_ in report.scores.items()
        for line in score_.wrong
    }


def test_recall_is_exactly_what_plan_md_records(report: Report) -> None:
    """Every defect the corpus labels is caught, except the ones named above."""
    assert _missed(report) == set(KNOWN_MISSES), report.table()


def test_false_positives_are_exactly_what_plan_md_records(report: Report) -> None:
    """Nothing labelled intentional or unlabelled is reported, except as named."""
    assert _wrong(report) == set(KNOWN_FALSE_POSITIVES), report.table()


def test_the_corpus_has_both_halves(report: Report) -> None:
    """A corpus of only defects would score a tool that reports everything at
    100%, and one of only intentional oddities would score a tool that reports
    nothing at 0 false positives. Neither number means anything without the
    other."""
    labels = [label for case in CASES for label in case.labels]
    assert sum(label.truth == "defect" for label in labels) >= 20
    assert sum(label.truth == "intentional" for label in labels) >= 9
    assert report.defects >= 20


def test_every_rule_in_the_corpus_is_scored_on_a_defect(report: Report) -> None:
    """A rule with no labelled defect has a recall of 100% by definition, which
    is the kind of number §8 says not to quote."""
    unscored = sorted(
        rule_id for rule_id, score_ in report.scores.items() if not score_.defects
    )
    assert not unscored, unscored
