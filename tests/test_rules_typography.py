"""Typography rule tests.

Two tests for every rule: it catches the violation the fixture seeds for it, and
it says nothing about the clean deck. The second is the one that matters -- a
rule that fires on correct slides trains the user to skim past the report -- so
there is also a category-wide silence test and a test that a rule whose
convention was never learned is skipped rather than checked against a guess.

Where a test departs from "run the rule over the dirty deck", the departure is
explained in the test's own docstring, because each one records something the
fixture generator or the engine cannot currently express.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from tests.conftest import assert_silent_on_clean, findings_for, slide_indices
from tieout.fixtures.spec import default_defects
from tieout.model.deck import DeckModel, ShapeModel, SlideModel
from tieout.profile.schema import Profile
from tieout.rules import typography
from tieout.rules.base import clear_caches, rules_in_category, run_rules
from tieout.text import parse_number

#: Rule id -> the slide the fixture seeds its defect on.
SEEDED: dict[str, int | None] = {
    defect.rule_id: defect.slide_index for defect in default_defects()
}

#: Words the clean deck's prose uses that the bundled wordlist does not carry.
#: British ``-ise`` inflections, ordinary verb forms and finance vocabulary. They
#: are listed here rather than quietly added to a dictionary so that the gap is
#: visible and can be closed in one place once, either in the bundled list or in
#: the client dictionary the profile carries.
DICTIONARY_SHORTFALL: tuple[str, ...] = (
    "Confirmatory",
    "accrues",
    "ceding",
    "fifths",
    "forecloses",
    "maximises",
    "normalises",
    "optionality",
    "premia",
    "reasonableness",
    "renews",
)


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    """Drop the memoised furniture between tests.

    The cache is keyed on ``id(deck), id(profile)`` and every test here builds a
    fresh profile, so a recycled address could otherwise hand one test the
    furniture derived for another.
    """
    clear_caches()
    yield
    clear_caches()


def _rewrite(
    deck: DeckModel,
    slide_index: int,
    shape_name: str,
    rewrite: Callable[[str], str],
) -> DeckModel:
    """A copy of ``deck`` with one shape's paragraph text passed through ``rewrite``.

    Editing the model rather than the fixture generator keeps the change inside
    this file, which is what the two cases below need: text the generator cannot
    currently produce.
    """
    shapes: list[ShapeModel] = []
    slides: list[SlideModel] = []
    for slide in deck.slides:
        if slide.index != slide_index:
            slides.append(slide)
            continue
        shapes = []
        for shape in slide.shapes:
            if shape.ref.name == shape_name:
                paragraphs = tuple(
                    type(paragraph)(
                        **{
                            **{
                                field: getattr(paragraph, field)
                                for field in paragraph.__slots__
                                if field != "runs"
                            },
                            "runs": tuple(
                                type(run)(
                                    text=rewrite(run.text),
                                    font=run.font,
                                    hyperlink=run.hyperlink,
                                )
                                for run in paragraph.runs
                            ),
                        }
                    )
                    for paragraph in shape.text_frame_paragraphs
                )
                shape = _replace_shape(shape, paragraphs)
            shapes.append(shape)
        slides.append(_replace_slide(slide, tuple(shapes)))
    return _replace_deck(deck, tuple(slides))


def _replace_shape(shape: ShapeModel, paragraphs: tuple[object, ...]) -> ShapeModel:
    from dataclasses import replace

    return replace(shape, text_frame_paragraphs=paragraphs)  # type: ignore[arg-type]


def _replace_slide(slide: SlideModel, shapes: tuple[ShapeModel, ...]) -> SlideModel:
    from dataclasses import replace

    return replace(slide, shapes=shapes)


def _replace_deck(deck: DeckModel, slides: tuple[SlideModel, ...]) -> DeckModel:
    from dataclasses import replace

    return replace(deck, slides=slides)


# --------------------------------------------------------------------------------------
# TY-001 quotes
# --------------------------------------------------------------------------------------


def test_ty001_catches_a_straight_apostrophe(dirty_deck, reference_profile):
    """A straight apostrophe on the seeded slide, against a curly convention.

    The fixture generator seeds the defect as ``the company's position`` and then
    runs the whole string through its own straight-to-curly conversion, so the
    deck it writes contains no straight glyph anywhere and the seeded TY-001
    defect never reaches the file. The apostrophe is put back here, on the slide
    the spec names, so the rule is still tested against the intended defect.
    """
    slide = SEEDED["TY-001"]
    assert slide is not None
    deck = _rewrite(dirty_deck, slide, "Column 1", lambda text: text.replace("’", "'"))

    result = run_rules(deck, reference_profile, include=["TY-001"])
    findings = findings_for(result, "TY-001")

    assert slide_indices(findings) == {slide}
    assert "straight" in findings[0].message
    assert findings[0].expected == "curly quotes and apostrophes"


def test_ty001_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-001"])
    assert_silent_on_clean(result, "TY-001")


# --------------------------------------------------------------------------------------
# TY-002 whitespace
# --------------------------------------------------------------------------------------


def test_ty002_catches_double_space_and_space_before_a_stop(dirty_deck, reference_profile):
    result = run_rules(dirty_deck, reference_profile, include=["TY-002"])
    findings = findings_for(result, "TY-002")

    assert slide_indices(findings) == {SEEDED["TY-002"]}
    assert len(findings) == 1, "TY-002 must cluster to one finding per slide"
    assert "double space" in findings[0].message
    assert "space before punctuation" in findings[0].message


def test_ty002_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-002"])
    assert_silent_on_clean(result, "TY-002")


def test_ty002_ignores_alignment_after_a_list_enumerator(dirty_deck, reference_profile):
    """The agenda aligns its headings with spaces after ``I.``, ``II.`` and so on.

    That is deliberate column alignment, not a typing slip, and the reference
    deck's own contents page depends on the rule not reporting it.
    """
    result = run_rules(dirty_deck, reference_profile, include=["TY-002"])
    agenda = [
        slide.index for slide in dirty_deck.slides if slide.archetype == "agenda"
    ]
    assert agenda, "the fixture should carry an agenda slide"
    assert slide_indices(findings_for(result, "TY-002")).isdisjoint(agenda)


# --------------------------------------------------------------------------------------
# TY-003 bullet terminal punctuation
# --------------------------------------------------------------------------------------


def test_ty003_catches_a_stop_in_an_unpunctuated_list(dirty_deck, reference_profile):
    result = run_rules(dirty_deck, reference_profile, include=["TY-003"])
    findings = findings_for(result, "TY-003")
    seeded = SEEDED["TY-003"]

    assert seeded in slide_indices(findings)
    for finding in findings:
        if finding.slide_index == seeded:
            assert finding.expected == "none"
            assert "period" in finding.message
            break


def test_ty003_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-003"])
    assert_silent_on_clean(result, "TY-003")


def test_ty003_leaves_a_fully_punctuated_list_alone(clean_deck, reference_profile):
    """A list punctuated throughout is a decision, not a slip.

    Every bullet of one list is given a full stop, against the deck's ``none``
    convention. The rule must stay silent and record the list as unchecked: the
    defect it looks for is one stop among several bullets without them.
    """
    deck = _rewrite(clean_deck, 16, "Column 1", lambda text: f"{text}.")

    result = run_rules(deck, reference_profile, include=["TY-003"])

    assert findings_for(result, "TY-003") == []
    reasons = [note.reason for note in result.unchecked if note.slide_index == 16]
    assert any("list-level choice" in reason for reason in reasons)


# --------------------------------------------------------------------------------------
# TY-004 title capitalisation
# --------------------------------------------------------------------------------------


def test_ty004_catches_a_title_cased_heading(dirty_deck, reference_profile):
    result = run_rules(dirty_deck, reference_profile, include=["TY-004"])
    findings = findings_for(result, "TY-004")

    assert slide_indices(findings) == {SEEDED["TY-004"]}
    assert findings[0].expected == "sentence case"
    assert "title case" in findings[0].message


def test_ty004_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-004"])
    assert_silent_on_clean(result, "TY-004")


def test_ty004_records_the_headings_it_does_not_judge(clean_deck, reference_profile):
    """Fixed document labels and two-word titles are recorded, never passed."""
    result = run_rules(clean_deck, reference_profile, include=["TY-004"])
    notes = {note.slide_index: note.reason for note in result.unchecked}

    agenda = next(s.index for s in clean_deck.slides if s.archetype == "agenda")
    content = next(s.index for s in clean_deck.slides if s.archetype == "content")
    assert "fixed document label" in notes[agenda]
    assert content not in notes, "a prose headline should be judged, not skipped"


# --------------------------------------------------------------------------------------
# TY-005 canonical terms
# --------------------------------------------------------------------------------------


def test_ty005_catches_a_non_canonical_variant(dirty_deck, reference_profile):
    """The seeded variant is not in the profile's variant list.

    ``canon_terms`` maps "Ashcombe Partners" to an empty list, so catching
    "Ashcombe partners" proves the rule groups surface forms by ``canon_key``
    rather than only matching variants someone thought to record.
    """
    result = run_rules(dirty_deck, reference_profile, include=["TY-005"])
    findings = findings_for(result, "TY-005")

    assert slide_indices(findings) == {SEEDED["TY-005"]}
    assert findings[0].expected == "Ashcombe Partners"
    assert findings[0].measured == "Ashcombe partners"
    assert findings[0].severity == "major"


def test_ty005_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-005"])
    assert_silent_on_clean(result, "TY-005")


# --------------------------------------------------------------------------------------
# TY-006 number formatting within a column
# --------------------------------------------------------------------------------------


def test_ty006_catches_mixed_decimal_places_in_one_column(dirty_deck, reference_profile):
    result = run_rules(dirty_deck, reference_profile, include=["TY-006"])
    findings = findings_for(result, "TY-006")

    assert slide_indices(findings) == {SEEDED["TY-006"]}
    assert len(findings) == 1, "one finding per offending column"
    assert "decimal places" in findings[0].message
    assert "'Revenue'" in findings[0].message


def test_ty006_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-006"])
    assert_silent_on_clean(result, "TY-006")


def test_ty006_compares_percentages_and_multiples_separately():
    """A column holding both a margin and a multiple is legitimately two formats."""
    readings = [parse_number(raw) for raw in ("15%", "9.2x", "8.4x")]
    assert all(reading is not None for reading in readings)

    mixed = typography._disagreements([r for r in readings if r is not None])
    assert mixed == []

    within_one_suffix = [parse_number("15%"), parse_number("16.8%")]
    kinds = typography._disagreements(
        [r for r in within_one_suffix if r is not None]
    )
    assert [kind for kind, _ in kinds] == ["decimal places"]


# --------------------------------------------------------------------------------------
# TY-007 currency notation
# --------------------------------------------------------------------------------------


def test_ty007_catches_a_currency_written_the_wrong_way(dirty_deck, reference_profile):
    result = run_rules(dirty_deck, reference_profile, include=["TY-007"])
    findings = findings_for(result, "TY-007")

    assert slide_indices(findings) == {SEEDED["TY-007"]}
    assert len(findings) == 1, "one finding per slide per offending marker"
    assert findings[0].measured is not None
    assert findings[0].measured.startswith("USD")


def test_ty007_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-007"])
    assert_silent_on_clean(result, "TY-007")


# --------------------------------------------------------------------------------------
# TY-008 date format
# --------------------------------------------------------------------------------------


def test_ty008_catches_a_second_date_format(dirty_deck, reference_profile):
    result = run_rules(dirty_deck, reference_profile, include=["TY-008"])
    findings = findings_for(result, "TY-008")

    assert slide_indices(findings) == {SEEDED["TY-008"]}
    assert findings[0].expected == "%d-%B-%Y"
    assert "%m/%d/%Y" in findings[0].message


def test_ty008_silent_on_clean(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-008"])
    assert_silent_on_clean(result, "TY-008")


# --------------------------------------------------------------------------------------
# TY-009 spelling
# --------------------------------------------------------------------------------------


def _enable_spelling(monkeypatch: pytest.MonkeyPatch, profile: Profile) -> None:
    """Turn TY-009 on for one test.

    Removing it from ``rules.disabled`` is not enough: the engine falls back to
    the rule's ``default_enabled``, which is False for TY-009, and neither the
    profile nor ``run_rules``' ``include`` patterns can override that. The class
    attribute is patched instead, so the rule still runs through the engine and
    the test exercises ``requires``, provenance and clustering as a real audit
    would. ``monkeypatch`` restores it afterwards.
    """
    profile.rules.disabled = [
        rule_id for rule_id in profile.rules.disabled if rule_id != "TY-009"
    ]
    monkeypatch.setattr(typography.Spelling, "default_enabled", True)


def test_ty009_catches_a_misspelled_word(dirty_deck, reference_profile, monkeypatch):
    _enable_spelling(monkeypatch, reference_profile)

    result = run_rules(dirty_deck, reference_profile, include=["TY-009"])
    findings = findings_for(result, "TY-009")
    seeded = SEEDED["TY-009"]

    assert "TY-009" in result.rules_run
    assert seeded in slide_indices(findings)
    seeded_findings = [f for f in findings if f.slide_index == seeded]
    assert "subbject" in seeded_findings[0].message


def test_ty009_silent_on_clean_with_the_client_dictionary(
    clean_deck, reference_profile, monkeypatch
):
    """With the shortfall words in the client dictionary, nothing else fires.

    This is the rule's real test: once the words the bundled list happens to
    lack are declared, twenty-six slides of banking prose -- proper nouns,
    possessives, hyphenation, all-caps acronyms, a legal disclaimer -- produce no
    spelling findings at all.
    """
    _enable_spelling(monkeypatch, reference_profile)
    reference_profile.hygiene.dictionary = [
        *reference_profile.hygiene.dictionary,
        *DICTIONARY_SHORTFALL,
    ]

    result = run_rules(clean_deck, reference_profile, include=["TY-009"])
    assert_silent_on_clean(result, "TY-009")


def test_ty009_bundled_dictionary_covers_the_clean_deck(
    clean_deck, reference_profile, monkeypatch
):
    """The bundled wordlist plus the profile's own dictionary, with no help.

    Kept as a strict expected failure so the shortfall is recorded rather than
    papered over: when the list is extended this test passes and the marker
    comes off.
    """
    _enable_spelling(monkeypatch, reference_profile)

    result = run_rules(clean_deck, reference_profile, include=["TY-009"])
    assert findings_for(result, "TY-009") == []


def test_ty009_is_disabled_unless_asked_for(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-009"])

    assert "TY-009" not in result.rules_run
    assert [skip.rule_id for skip in result.rules_skipped] == ["TY-009"]


# --------------------------------------------------------------------------------------
# Category-wide behaviour
# --------------------------------------------------------------------------------------


def test_the_whole_category_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["TY-*"])

    assert result.findings == [], "; ".join(
        f"{f.rule_id} on slide {f.slide_index}: {f.message}" for f in result.findings
    )
    assert result.rules_run, "the category should have run, not been skipped wholesale"


@pytest.mark.parametrize(
    ("rule_id", "field", "unlearned"),
    [
        ("TY-001", "quotes", None),
        ("TY-003", "bullet_terminal_punctuation", None),
        ("TY-004", "title_case", None),
        ("TY-005", "canon_terms", {}),
        ("TY-006", "decimal_places_by_column", None),
        ("TY-007", "currency_pattern", None),
        ("TY-008", "date_format", None),
    ],
)
def test_a_rule_is_skipped_when_its_convention_was_not_learned(
    clean_deck, reference_profile, rule_id, field, unlearned
):
    """"Not learned" must become "not checked", never "checked against a guess"."""
    setattr(reference_profile.typography, field, unlearned)

    result = run_rules(clean_deck, reference_profile, include=[rule_id])

    assert rule_id not in result.rules_run
    assert [skip.rule_id for skip in result.rules_skipped] == [rule_id]
    assert f"typography.{field}" in result.rules_skipped[0].reason


def test_every_finding_carries_its_provenance(dirty_deck, reference_profile, monkeypatch):
    """A finding whose expectation came from the client's own deck must say so."""
    _enable_spelling(monkeypatch, reference_profile)

    result = run_rules(dirty_deck, reference_profile, include=["TY-*"])

    assert result.findings
    missing = [f.rule_id for f in result.findings if not f.expected_provenance]
    assert missing == []


def test_every_rule_documents_what_it_measures_and_how_it_misfires():
    """Section 9: each rule states its measurement and its false-positive mode."""
    for rule_cls in rules_in_category("typography"):
        docstring = (rule_cls.__doc__ or "").casefold()
        assert rule_cls.summary, f"{rule_cls.id} has no summary"
        assert "measures:" in docstring, f"{rule_cls.id} does not say what it measures"
        assert "false positive" in docstring, (
            f"{rule_cls.id} does not state its false-positive mode"
        )
