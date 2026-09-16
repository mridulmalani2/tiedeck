"""Consistency rule tests.

These rules are the ones a banker would actually thank you for, and also the
ones most able to embarrass the tool: a false "these two figures disagree" on a
deck where they legitimately differ is worse than silence, because it sends
someone to check a number that was right.

So alongside the seeded-defect and clean-deck tests, this module builds small
purpose-built decks to pin the label-matching behaviour exactly — which pairs
compare, which are too generic to compare, and which disagreements are
arithmetic rather than opinion.
"""

from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.util import Emu, Pt

from tests.conftest import assert_silent_on_clean, findings_for, slide_indices
from tieout.fixtures.spec import default_defects
from tieout.model.loader import load_deck
from tieout.rules.base import run_rules
from tieout.rules.consistency import (
    _is_total_label,
    iter_numeric_cells,
    normalise_label,
)

SEEDED = {d.rule_id: d.slide_index for d in default_defects() if d.variant is None}
CONSISTENCY_RULE_IDS = ("CO-001", "CO-002", "CO-003")


def _run(deck, profile, rule_id):
    return run_rules(deck, profile, include=[rule_id])


# --------------------------------------------------------------------------------------
# Purpose-built decks
# --------------------------------------------------------------------------------------


def _deck_with_tables(path, tables):
    """Build a deck of one-table slides. ``tables`` is a list of row lists."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    for index, rows in enumerate(tables):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        frame = slide.shapes.add_table(
            len(rows), len(rows[0]), Pt(36), Pt(120), Pt(888), Pt(24 * len(rows))
        )
        frame.name = f"Table {index + 1}"
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                frame.table.cell(row_index, column_index).text = str(value)
    presentation.save(str(path))
    return load_deck(path)


_PROJECTIONS = [
    ["Fiscal year", "Revenue", "EBITDA", "Margin"],
    ["2024A", "1,562", "263", "16.8%"],
    ["2025A", "1,908", "351", "18.4%"],
]


@pytest.fixture
def agreeing(tmp_path):
    """Two tables restating the same figures identically. Must be silent."""
    return _deck_with_tables(
        tmp_path / "agree.pptx",
        [
            _PROJECTIONS,
            [["Fiscal year", "EBITDA"], ["2025A", "351"]],
        ],
    )


@pytest.fixture
def disagreeing(tmp_path):
    """Two tables restating the same figure differently."""
    return _deck_with_tables(
        tmp_path / "disagree.pptx",
        [
            _PROJECTIONS,
            [["Fiscal year", "EBITDA"], ["2025A", "375"]],
        ],
    )


# --------------------------------------------------------------------------------------
# Registration and documentation
# --------------------------------------------------------------------------------------


def test_every_consistency_rule_is_registered_and_documented():
    from tieout.rules.base import load_all_rules

    registry = load_all_rules()
    for rule_id in CONSISTENCY_RULE_IDS:
        assert rule_id in registry
        rule = registry[rule_id]
        assert rule.category == "consistency"
        assert rule.summary
        assert rule.__doc__ and "false-positive mode" in rule.__doc__


def test_the_consistency_rules_need_nothing_from_the_profile():
    """Internal arithmetic is not a house style, so these rules run on any deck
    with or without a learned profile."""
    from tieout.rules.base import load_all_rules

    registry = load_all_rules()
    for rule_id in CONSISTENCY_RULE_IDS:
        assert registry[rule_id].requires == ()


# --------------------------------------------------------------------------------------
# CO-001 contradictory figure
# --------------------------------------------------------------------------------------


def test_co001_catches_the_seeded_contradiction(dirty_deck, reference_profile):
    findings = findings_for(_run(dirty_deck, reference_profile, "CO-001"), "CO-001")
    assert SEEDED["CO-001"] in slide_indices(findings)
    message = next(f for f in findings if f.slide_index == SEEDED["CO-001"]).message
    assert "375" in message and "351" in message
    assert "slide 6" in message, "it must say where the other figure lives"


def test_co001_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    assert_silent_on_clean(_run(clean_deck, reference_profile, "CO-001"), "CO-001")


def test_co001_says_nothing_when_two_tables_agree(agreeing, reference_profile):
    assert findings_for(_run(agreeing, reference_profile, "CO-001"), "CO-001") == []


def test_co001_reports_when_two_tables_disagree(disagreeing, reference_profile):
    findings = findings_for(_run(disagreeing, reference_profile, "CO-001"), "CO-001")
    assert len(findings) == 1
    assert findings[0].measured == "375"


def test_co001_ignores_repetition_inside_one_table(tmp_path, reference_profile):
    """A label repeated within a single table is a layout artefact, not two
    statements of the same fact."""
    deck = _deck_with_tables(
        tmp_path / "one.pptx",
        [
            [
                ["Fiscal year", "EBITDA"],
                ["2025A", "351"],
                ["2025A", "375"],
            ]
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-001"), "CO-001") == []


def test_co001_will_not_compare_a_percentage_with_a_multiple(
    tmp_path, reference_profile
):
    """Under one label, 9.2x and 9.2% are different quantities. Comparing them
    would report a contradiction between two numbers that were never the same
    kind of thing."""
    deck = _deck_with_tables(
        tmp_path / "suffix.pptx",
        [
            [["Company", "Multiple"], ["Calderwood", "9.2x"]],
            [["Company", "Multiple"], ["Calderwood", "17.4%"]],
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-001"), "CO-001") == []


def test_co001_will_not_compare_on_a_generic_label(tmp_path, reference_profile):
    """"Value" identifies nothing. Keying on it is how a consistency rule starts
    reporting every table in a deck against every other."""
    deck = _deck_with_tables(
        tmp_path / "generic.pptx",
        [
            [["Item", "Value"], ["Other", "100"]],
            [["Item", "Value"], ["Other", "250"]],
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-001"), "CO-001") == []


def test_co001_leaves_a_pure_scale_error_to_co002(tmp_path, reference_profile):
    """A clean factor of 1000 is a units problem with a different fix, so it is
    reported once by CO-002 rather than twice by both rules."""
    deck = _deck_with_tables(
        tmp_path / "scale.pptx",
        [
            [["Fiscal year", "Revenue"], ["2025A", "1,908"]],
            [["Fiscal year", "Revenue"], ["2025A", "1,908,000"]],
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-001"), "CO-001") == []
    assert findings_for(_run(deck, reference_profile, "CO-002"), "CO-002")


def test_co001_skips_cells_that_are_not_numbers(tmp_path, reference_profile):
    deck = _deck_with_tables(
        tmp_path / "placeholders.pptx",
        [
            [["Objective", "Status"], ["Capital raised", "n.a."]],
            [["Objective", "Status"], ["Capital raised", "Full"]],
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-001"), "CO-001") == []


# --------------------------------------------------------------------------------------
# CO-002 scale mismatch
# --------------------------------------------------------------------------------------


def test_co002_catches_the_seeded_unit_error(dirty_deck, reference_profile):
    findings = findings_for(_run(dirty_deck, reference_profile, "CO-002"), "CO-002")
    assert SEEDED["CO-002"] in slide_indices(findings)
    message = next(f for f in findings if f.slide_index == SEEDED["CO-002"]).message
    assert "wrong unit" in message
    assert "1,000" in message or "factor of 1,000" in message


def test_co002_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    assert_silent_on_clean(_run(clean_deck, reference_profile, "CO-002"), "CO-002")


@pytest.mark.parametrize(
    ("other", "expected"),
    [("1,908,000", True), ("190,800", True), ("1,920", False), ("3,816", False)],
)
def test_co002_only_fires_on_a_clean_factor(
    tmp_path, reference_profile, other, expected
):
    """A thousand-fold or hundred-fold difference is a unit error. A doubling is a
    disagreement, and CO-001's business."""
    deck = _deck_with_tables(
        tmp_path / f"factor-{other.replace(',', '')}.pptx",
        [
            [["Fiscal year", "Revenue"], ["2025A", "1,908"]],
            [["Fiscal year", "Revenue"], ["2025A", other]],
        ],
    )
    fired = bool(findings_for(_run(deck, reference_profile, "CO-002"), "CO-002"))
    assert fired is expected


# --------------------------------------------------------------------------------------
# CO-003 totals
# --------------------------------------------------------------------------------------


def test_co003_catches_the_seeded_broken_total(dirty_deck, reference_profile):
    findings = findings_for(_run(dirty_deck, reference_profile, "CO-003"), "CO-003")
    assert SEEDED["CO-003"] in slide_indices(findings)
    message = next(f for f in findings if f.slide_index == SEEDED["CO-003"]).message
    assert "9,900" in message and "9,828" in message


def test_co003_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    """The clean deck's projections table carries a cumulative row whose
    arithmetic is correct, so this is a real verification rather than a rule
    passing because it found nothing to check."""
    assert_silent_on_clean(_run(clean_deck, reference_profile, "CO-003"), "CO-003")


def test_the_clean_deck_actually_contains_a_total_row_to_verify(clean_deck):
    """Guards the test above from silently becoming vacuous."""
    totals = [
        cell
        for slide in clean_deck.slides
        for shape in slide.tables
        if shape.table
        for cell in shape.table.cells
        if cell.column == 0 and _is_total_label(normalise_label(cell.text))
    ]
    assert totals, "the fixture is supposed to carry a total row for CO-003 to check"


def test_co003_tolerates_rounding(tmp_path, reference_profile):
    """Five figures each rounded to the nearest whole number can legitimately sum
    2.5 away from their rounded total. A rule demanding exactness would report
    every correctly-rounded table in banking."""
    deck = _deck_with_tables(
        tmp_path / "rounding.pptx",
        [
            [
                ["Segment", "Revenue"],
                ["North", "10"],
                ["South", "10"],
                ["East", "10"],
                ["West", "10"],
                ["Total", "42"],
            ]
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-003"), "CO-003") == []


def test_co003_reports_a_total_beyond_the_rounding_tolerance(
    tmp_path, reference_profile
):
    deck = _deck_with_tables(
        tmp_path / "broken.pptx",
        [
            [
                ["Segment", "Revenue"],
                ["North", "10"],
                ["South", "10"],
                ["East", "10"],
                ["West", "10"],
                ["Total", "60"],
            ]
        ],
    )
    findings = findings_for(_run(deck, reference_profile, "CO-003"), "CO-003")
    assert len(findings) == 1
    assert "60" in findings[0].message and "40" in findings[0].message


def test_co003_does_not_add_up_percentages(tmp_path, reference_profile):
    """A "Total" over a margin column is a weighted average, not a sum, and
    adding the percentages would report every margin table ever drawn."""
    deck = _deck_with_tables(
        tmp_path / "percent.pptx",
        [
            [
                ["Segment", "Margin"],
                ["North", "15.0%"],
                ["South", "16.0%"],
                ["East", "17.0%"],
                ["Total", "16.0%"],
            ]
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-003"), "CO-003") == []


def test_co003_needs_at_least_three_addends(tmp_path, reference_profile):
    """Two numbers and a total is as often a subtraction or a comparison."""
    deck = _deck_with_tables(
        tmp_path / "two.pptx",
        [[["Segment", "Revenue"], ["North", "10"], ["Total", "40"]]],
    )
    assert findings_for(_run(deck, reference_profile, "CO-003"), "CO-003") == []


def test_co003_skips_a_column_holding_non_numeric_rows(tmp_path, reference_profile):
    """If a column mixes prose and figures it is not a sum, whatever the total row
    says."""
    deck = _deck_with_tables(
        tmp_path / "mixed.pptx",
        [
            [
                ["Segment", "Revenue"],
                ["North", "10"],
                ["South", "Pending"],
                ["East", "10"],
                ["West", "10"],
                ["Total", "60"],
            ]
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-003"), "CO-003") == []


def test_co003_ignores_a_label_that_merely_starts_with_total(
    tmp_path, reference_profile
):
    """"Total addressable market" is a metric, not an assertion of arithmetic."""
    deck = _deck_with_tables(
        tmp_path / "tam.pptx",
        [
            [
                ["Segment", "Value"],
                ["North", "10"],
                ["South", "10"],
                ["East", "10"],
                ["Total addressable market", "900"],
            ]
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-003"), "CO-003") == []


def test_co003_excludes_a_subtotal_from_the_grand_total(tmp_path, reference_profile):
    """A grand total sums the line items, not the line items plus a subtotal."""
    deck = _deck_with_tables(
        tmp_path / "subtotal.pptx",
        [
            [
                ["Segment", "Revenue"],
                ["North", "10"],
                ["South", "10"],
                ["Sum", "20"],
                ["East", "10"],
                ["West", "10"],
                ["Total", "40"],
            ]
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-003"), "CO-003") == []


# --------------------------------------------------------------------------------------
# Shared machinery and the category guard
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EBITDA(1)", "ebitda"),
        ("EBITDA ", "ebitda"),
        ("Revenue*", "revenue"),
        ("Free cash flow", "free cash flow"),
        ("FY24", "fy24"),
        ("Q1 2026", "q1 2026"),
        ("Top 10", "top 10"),
    ],
)
def test_label_normalisation(raw, expected):
    """A bare trailing number is part of the label, never a footnote marker."""
    assert normalise_label(raw) == expected


def test_cells_are_labelled_by_their_row_and_column(clean_deck):
    cells = list(iter_numeric_cells(clean_deck))
    assert cells, "the fixture has numeric tables"
    keyed = {cell.key for cell in cells}
    assert ("2025a", "revenue") in keyed
    assert ("2025a", "ebitda") in keyed
    for cell in cells:
        assert cell.slide_index >= 1
        assert cell.reading is not None


def test_the_consistency_category_is_silent_on_the_clean_deck(
    clean_deck, reference_profile
):
    result = run_rules(clean_deck, reference_profile, include=["CO-*"])
    assert result.findings == [], "; ".join(
        f"slide {f.slide_index} {f.rule_id}: {f.message}" for f in result.findings
    )


def test_a_deck_with_no_tables_is_handled(tmp_path, reference_profile):
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    path = tmp_path / "empty.pptx"
    presentation.save(str(path))
    result = run_rules(load_deck(path), reference_profile, include=["CO-*"])
    assert result.findings == []
