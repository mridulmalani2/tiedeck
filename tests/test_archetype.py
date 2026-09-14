"""Archetype classification.

Archetypes are what let the learning engine work without interrogating the user
about every exception. "The logo is at x=852" is false for a deck; "the logo is
top-right on content slides and centred on dividers" is true, and deriving the
second requires knowing which slides are which.

So the first test here -- every slide in the reference deck classified as its
specification says -- gates the whole learning engine. A misclassified divider
pollutes the content archetype's logo rule with a centred placement.
"""

from __future__ import annotations

import pytest

from tieout.model.archetype import (
    ARCHETYPES,
    CONTENT_ARCHETYPES,
    SPARSE_ARCHETYPES,
    classify_deck,
    classify_slide,
)


def test_every_slide_matches_the_specification(clean_deck, spec):
    """The specification declares what each of the 26 slides is; the classifier
    has to agree, slide by slide."""
    mismatches = [
        (
            slide.index,
            slide.archetype,
            spec.slide(slide.index).archetype,
            slide.archetype_reasons,
        )
        for slide in clean_deck.slides
        if spec.slide(slide.index) is not None
        and slide.archetype != spec.slide(slide.index).archetype
    ]
    assert not mismatches, "\n".join(
        f"slide {index}: got {got!r}, expected {want!r} because {reasons}"
        for index, got, want, reasons in mismatches
    )


def test_the_whole_deck_is_classified_and_nothing_is_unknown(clean_deck):
    """An unknown slide is excluded from learning, so an unknown in a clean
    reference deck is evidence lost rather than a neutral outcome."""
    unknown = [s.index for s in clean_deck.slides if s.archetype == "unknown"]
    assert not unknown, f"slides {unknown} could not be classified"
    assert len(clean_deck.slides) == 26


def test_every_assignment_records_its_reasons(clean_deck):
    """A user who disagrees has to be able to see why, and correct it."""
    for slide in clean_deck.slides:
        assert slide.archetype in ARCHETYPES
        assert slide.archetype_confidence in ("high", "medium", "low")
        assert slide.archetype_reasons, f"slide {slide.index} has no stated reasons"


def test_most_of_the_deck_is_classified_with_high_confidence(clean_deck):
    high = [s for s in clean_deck.slides if s.archetype_confidence == "high"]
    assert len(high) >= 20, (
        "a template-built deck should classify confidently; "
        f"only {len(high)} of {len(clean_deck.slides)} did"
    )


def test_classification_is_deterministic(clean_deck):
    """Same deck, same answer, every run. A QA tool that disagrees with itself is
    worse than none."""
    first = {index: r.archetype for index, r in classify_deck(clean_deck).items()}
    second = {index: r.archetype for index, r in classify_deck(clean_deck).items()}
    assert first == second


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (1, "title"),
        (2, "agenda"),
        (3, "section_divider"),
        (6, "table_heavy"),
        (8, "chart_heavy"),
        (23, "appendix_divider"),
        (25, "disclaimer"),
    ],
)
def test_each_archetype_is_reached_by_at_least_one_slide(index, expected, clean_deck):
    """Named cases, so a regression says which archetype broke."""
    slide = clean_deck.slide(index)
    assert slide is not None
    assert slide.archetype == expected


def test_the_archetype_groupings_are_consistent():
    assert set(ARCHETYPES) >= CONTENT_ARCHETYPES
    assert set(ARCHETYPES) >= SPARSE_ARCHETYPES
    assert not CONTENT_ARCHETYPES & SPARSE_ARCHETYPES, (
        "an archetype cannot be both content-bearing and too sparse to measure"
    )


def test_a_table_slide_is_not_classified_as_plain_content(clean_deck):
    """The distinction earns its keep: a table slide's margins and font roles
    differ from a bulleted one's, and folding them together widens both."""
    for index in (6, 12, 18):
        slide = clean_deck.slide(index)
        assert slide is not None
        assert slide.tables, f"slide {index} is supposed to carry a table"
        assert slide.archetype == "table_heavy"


def test_an_agenda_is_recognised_from_its_title(clean_deck):
    slide = clean_deck.slide(2)
    assert slide is not None
    assert "contents" in slide.title_text.casefold()
    assert any("agenda pattern" in reason for reason in slide.archetype_reasons)


def test_a_disclaimer_is_recognised_even_when_titled_differently(clean_deck):
    """Slide 26 is headed "Important Notice" rather than "Disclaimer"."""
    slide = clean_deck.slide(26)
    assert slide is not None
    assert slide.archetype == "disclaimer"


def test_a_slide_is_classified_the_same_in_isolation_as_in_the_deck(clean_deck):
    """Only the after-a-divider signal depends on context, so for every other
    slide the two paths must agree -- otherwise the report and the learner could
    disagree about the same slide."""
    previous: str | None = None
    for slide in clean_deck.slides:
        isolated = classify_slide(slide, clean_deck, previous)
        assert isolated.archetype == slide.archetype, (
            f"slide {slide.index}: {isolated.archetype} in isolation, "
            f"{slide.archetype} in the deck"
        )
        previous = slide.archetype


def test_an_empty_slide_is_unknown_rather_than_content(clean_deck):
    """A slide the classifier cannot categorise must be excluded from learning,
    not guessed at."""
    slide = clean_deck.slide(4)
    assert slide is not None
    original = slide.shapes
    try:
        slide.shapes = ()
        result = classify_slide(slide, clean_deck, None)
        assert result.archetype == "unknown"
        assert result.confidence == "high", "being sure it is unknown is not a guess"
    finally:
        slide.shapes = original
