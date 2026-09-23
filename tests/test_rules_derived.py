"""Derived-figure rule tests.

Each rule is tested four ways, and the last two matter most.

1. It catches its **seeded defect on the reference deck** — real material, built
   by the generator, not a fixture written to suit the rule.
2. It catches the same defect in a small purpose-built deck, so a failure says
   which behaviour broke rather than only that the fixture moved.
3. It is **silent on the clean deck**, and on a purpose-built deck where the
   arithmetic is right. A rule that catches its seed but also fires on correct
   slides is worse than no rule.
4. It **refuses rather than guesses** where an input is missing or ambiguous,
   and the refusal is recorded as unchecked rather than swallowed. For a
   pre-send tool "I could not verify this" and "this is fine" must never look
   the same.

PLAN.md §8: "every rule in §5.4 gets a seeded defect it must catch and a clean
deck it must stay silent on", because twice a test has passed for the wrong
reason and the third would be a tie-out rule agreeing with a bug in the index.
"""

from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Pt

from tests.conftest import assert_silent_on_clean, findings_for
from tieout.fixtures.spec import default_defects
from tieout.model.loader import load_deck
from tieout.rules.base import run_rules

DERIVED_RULE_IDS = ("CO-004", "CO-005", "CO-006", "CO-007", "CO-008", "CO-009")

SEEDED = {d.rule_id: d.slide_index for d in default_defects() if d.variant is None}


def _run(deck, profile, rule_id):
    return run_rules(deck, profile, include=[rule_id])


def _deck(path, slides):
    """Build a deck. Each slide is ``(rows, footnote)``; rows may be None."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    for index, (rows, footnote) in enumerate(slides):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        if rows:
            frame = slide.shapes.add_table(
                len(rows), len(rows[0]), Pt(36), Pt(120), Pt(600), Pt(20 * len(rows))
            )
            frame.name = f"Table {index + 1}"
            for row_index, row in enumerate(rows):
                for column_index, value in enumerate(row):
                    frame.table.cell(row_index, column_index).text = str(value)
        if footnote:
            box = slide.shapes.add_textbox(Pt(36), Pt(420), Pt(600), Pt(20))
            box.text_frame.text = footnote
    presentation.save(str(path))
    return load_deck(path)


# --------------------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------------------


def test_every_derived_rule_is_registered_and_documented():
    from tieout.rules.base import load_all_rules

    registry = load_all_rules()
    for rule_id in DERIVED_RULE_IDS:
        assert rule_id in registry, rule_id
        rule = registry[rule_id]
        assert rule.category == "consistency"
        assert rule.summary
        assert rule.__doc__ and "false-positive mode" in rule.__doc__, rule_id


def test_the_derived_rules_need_nothing_from_the_profile():
    """Arithmetic on the deck's own figures. A rule that needed a learned
    expectation here would be asserting a house style, not a fact."""
    from tieout.rules.base import load_all_rules

    registry = load_all_rules()
    for rule_id in DERIVED_RULE_IDS:
        assert registry[rule_id].requires == ()


@pytest.mark.parametrize("rule_id", DERIVED_RULE_IDS)
def test_each_derived_rule_is_silent_on_the_clean_deck(
    clean_deck, reference_profile, rule_id
):
    assert_silent_on_clean(_run(clean_deck, reference_profile, rule_id), rule_id)


@pytest.mark.parametrize("rule_id", DERIVED_RULE_IDS)
def test_each_derived_rule_catches_its_own_seeded_defect(
    dirty_deck, reference_profile, rule_id
):
    """On the generated reference deck, not on a fixture written to suit the
    rule. PLAN.md §5.6: until this existed, every rule here was tested only
    against fixtures written by the same person who wrote the rule."""
    findings = findings_for(_run(dirty_deck, reference_profile, rule_id), rule_id)
    assert findings, f"{rule_id} did not catch its seeded defect"
    assert {f.slide_index for f in findings} == {SEEDED[rule_id]}, (
        f"{rule_id} fired somewhere other than its seed: "
        + "; ".join(f"slide {f.slide_index}: {f.message}" for f in findings)
    )


@pytest.mark.parametrize("rule_id", DERIVED_RULE_IDS)
def test_each_seeded_defect_is_caught_by_its_own_rule_only(
    dirty_deck, reference_profile, rule_id
):
    """Each seed sits on its own slide, against a period or a label no other
    table carries, so a seed cannot be claimed by two rules. Without this, a
    recap stating a wrong margin for a year slide 6 covers is also a CO-001
    contradiction, and "catches its own" stops meaning anything."""
    slide = SEEDED[rule_id]
    others = [
        f
        for other in DERIVED_RULE_IDS
        if other != rule_id
        for f in findings_for(_run(dirty_deck, reference_profile, other), other)
        if f.slide_index == slide
    ]
    assert not others, (
        f"{rule_id}'s seed on slide {slide} was also reported by: "
        + "; ".join(f"{f.rule_id}: {f.message}" for f in others)
    )


# --------------------------------------------------------------------------------------
# CO-004 -- margins
# --------------------------------------------------------------------------------------

_MARGIN_HEADER = ["Fiscal year", "Revenue", "EBITDA", "Margin"]


def test_co004_reports_a_margin_that_does_not_equal_its_inputs(
    tmp_path, reference_profile
):
    deck = _deck(
        tmp_path / "margin.pptx",
        [([_MARGIN_HEADER, ["2025A", "1,908", "351", "24.0%"]], None)],
    )
    findings = findings_for(_run(deck, reference_profile, "CO-004"), "CO-004")
    assert findings
    assert "24.0%" in findings[0].message
    assert "18.4" in findings[0].message


def test_co004_states_both_of_its_inputs(tmp_path, reference_profile):
    """PLAN.md §5.4: each rule states its inputs in the evidence line. A finding
    that says only "the margin is wrong" sends the reader looking for the
    working, and they will not find it."""
    deck = _deck(
        tmp_path / "evidence.pptx",
        [([_MARGIN_HEADER, ["2025A", "1,908", "351", "24.0%"]], None)],
    )
    (finding,) = findings_for(_run(deck, reference_profile, "CO-004"), "CO-004")
    assert "ebitda 351" in finding.message
    assert "revenue 1,908" in finding.message
    assert "rounding" in (finding.expected or "")


def test_co004_is_silent_on_a_correct_margin(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "correct.pptx",
        [([_MARGIN_HEADER, ["2025A", "1,908", "351", "18.4%"]], None)],
    )
    assert findings_for(_run(deck, reference_profile, "CO-004"), "CO-004") == []


def test_co004_tolerates_rounding(tmp_path, reference_profile):
    """351 over 1,908 is 18.396%. Stated to one decimal it is 18.4%, and a rule
    demanding exactness would report every correctly-rounded table in banking."""
    deck = _deck(
        tmp_path / "rounded.pptx",
        [([_MARGIN_HEADER, ["2025A", "1,908", "351", "18.4%"]], None)],
    )
    assert findings_for(_run(deck, reference_profile, "CO-004"), "CO-004") == []


def test_co004_refuses_where_the_denominator_is_missing(tmp_path, reference_profile):
    """A comparables table states a margin with no revenue line. The rule says
    so rather than falling silent among the rules that ran."""
    deck = _deck(
        tmp_path / "nodenominator.pptx",
        [([["Company", "EBITDA", "Margin"], ["Calderwood", "912", "17.4%"]], None)],
    )
    result = _run(deck, reference_profile, "CO-004")
    assert result.findings == []
    assert result.unchecked, "a margin with no revenue must be recorded as unchecked"
    assert "revenue" in result.unchecked[0].reason


def test_co004_refuses_where_an_input_is_stated_two_ways(tmp_path, reference_profile):
    """Two revenues for one period is not a margin problem, and picking one of
    them would be the rule choosing which figure the deck meant.

    The margin's own table carries no revenue, so the search widens to the deck
    -- where the deck says two different things. A margin whose own table *does*
    carry the input never reaches this, which is the point of searching the
    nearest table first.
    """
    deck = _deck(
        tmp_path / "ambiguous.pptx",
        [
            ([["Fiscal year", "EBITDA", "Margin"], ["2025A", "351", "24.0%"]], None),
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], None),
            ([["Fiscal year", "Revenue"], ["2025A", "2,100"]], None),
        ],
    )
    result = _run(deck, reference_profile, "CO-004")
    assert result.findings == []
    assert any("more than one way" in u.reason for u in result.unchecked)


def test_co004_prefers_the_margins_own_table(tmp_path, reference_profile):
    """A segment margin checked against group revenue is arithmetic applied to
    two unrelated numbers. The table the margin sits in wins."""
    deck = _deck(
        tmp_path / "nearest.pptx",
        [
            ([_MARGIN_HEADER, ["2025A", "1,908", "351", "18.4%"]], None),
            ([["Fiscal year", "Revenue", "EBITDA", "Margin"],
              ["2026E", "900", "180", "20.0%"]], None),
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-004"), "CO-004") == []


# --------------------------------------------------------------------------------------
# CO-005 -- multiples
# --------------------------------------------------------------------------------------

_COMPS_HEADER = ["Company", "Enterprise value", "EBITDA", "Multiple"]


def test_co005_reports_a_multiple_that_does_not_tie(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "multiple.pptx",
        [([_COMPS_HEADER, ["Calderwood Logistics", "8,420", "912", "11.5x"]], None)],
    )
    findings = findings_for(_run(deck, reference_profile, "CO-005"), "CO-005")
    assert findings
    assert "11.5x" in findings[0].message
    assert "9.2" in findings[0].message


def test_co005_is_silent_on_a_correct_multiple(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "correct_multiple.pptx",
        [([_COMPS_HEADER, ["Calderwood Logistics", "8,420", "912", "9.2x"]], None)],
    )
    assert findings_for(_run(deck, reference_profile, "CO-005"), "CO-005") == []


def test_co005_keeps_each_company_to_its_own_row(tmp_path, reference_profile):
    """Every row of a comparables table carries the same three labels. Without
    the company as the scope, one company's EV would be divided by another's
    EBITDA and every row of every comps table in banking would be reported."""
    deck = _deck(
        tmp_path / "rows.pptx",
        [([
            _COMPS_HEADER,
            ["Calderwood Logistics", "8,420", "912", "9.2x"],
            ["Pemberton Freight", "6,180", "674", "9.2x"],
            ["Thorne Distribution", "4,935", "602", "8.2x"],
        ], None)],
    )
    assert findings_for(_run(deck, reference_profile, "CO-005"), "CO-005") == []


def test_co005_does_not_read_a_percentage_as_a_multiple(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "percent.pptx",
        [([["Company", "Enterprise value", "EBITDA", "Multiple"],
           ["Calderwood", "8,420", "912", "92.0%"]], None)],
    )
    assert findings_for(_run(deck, reference_profile, "CO-005"), "CO-005") == []


# --------------------------------------------------------------------------------------
# CO-006 -- growth and CAGR
# --------------------------------------------------------------------------------------


def _series_and_claim(path, claim):
    return _deck(
        path,
        [
            ([["Fiscal year", "Revenue"], ["2024A", "1,562"], ["2027E", "2,760"]], None),
            ([["Metric", "Value"], ["Revenue CAGR 2024A-2027E", claim]], None),
        ],
    )


def test_co006_reports_a_cagr_the_series_does_not_give(tmp_path, reference_profile):
    deck = _series_and_claim(tmp_path / "cagr.pptx", "30.0%")
    findings = findings_for(_run(deck, reference_profile, "CO-006"), "CO-006")
    assert findings
    assert "30.0%" in findings[0].message
    assert "20.9" in findings[0].message
    assert "3 year(s)" in findings[0].message


def test_co006_is_silent_on_a_correct_cagr(tmp_path, reference_profile):
    deck = _series_and_claim(tmp_path / "correct_cagr.pptx", "20.9%")
    assert findings_for(_run(deck, reference_profile, "CO-006"), "CO-006") == []


def test_co006_refuses_a_bare_cagr_naming_no_metric(tmp_path, reference_profile):
    """"CAGR" alone names no series, and picking one would be the rule choosing
    which of the deck's numbers to check against on the strength of nothing."""
    deck = _deck(
        tmp_path / "bare.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2024A", "1,562"], ["2027E", "2,760"]], None),
            ([["Metric", "Value"], ["CAGR 2024A-2027E", "30.0%"]], None),
        ],
    )
    result = _run(deck, reference_profile, "CO-006")
    assert result.findings == []


def test_co006_refuses_where_an_endpoint_is_missing(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "endpoint.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2024A", "1,562"]], None),
            ([["Metric", "Value"], ["Revenue CAGR 2024A-2027E", "30.0%"]], None),
        ],
    )
    result = _run(deck, reference_profile, "CO-006")
    assert result.findings == []
    assert result.unchecked


# --------------------------------------------------------------------------------------
# CO-007 -- bridges
# --------------------------------------------------------------------------------------


def _bridge(path, closing):
    return _deck(
        path,
        [([
            ["Step", "EBITDA movement"],
            ["Opening", "263"],
            ["Volume", "45"],
            ["Price", "30"],
            ["Cost", "13"],
            ["Closing", closing],
        ], None)],
    )


def test_co007_reports_a_bridge_that_does_not_carry(tmp_path, reference_profile):
    findings = findings_for(
        _run(_bridge(tmp_path / "bridge.pptx", "400"), reference_profile, "CO-007"),
        "CO-007",
    )
    assert findings
    assert "263" in findings[0].message
    assert "400" in findings[0].message
    assert "351" in findings[0].message


def test_co007_is_silent_on_a_bridge_that_carries(tmp_path, reference_profile):
    assert (
        findings_for(
            _run(_bridge(tmp_path / "ok.pptx", "351"), reference_profile, "CO-007"),
            "CO-007",
        )
        == []
    )


def test_co007_ignores_a_table_that_is_not_a_bridge(tmp_path, reference_profile):
    """Recognised by its ends. A table of figures that opens and closes on
    nothing in particular is not asserting that its rows add up."""
    deck = _deck(
        tmp_path / "notabridge.pptx",
        [([
            ["Fiscal year", "Revenue"],
            ["2023A", "1,284"],
            ["2024A", "1,562"],
            ["2025A", "1,908"],
            ["2026E", "2,314"],
        ], None)],
    )
    assert findings_for(_run(deck, reference_profile, "CO-007"), "CO-007") == []


def test_co007_reads_a_bridge_drawn_as_a_chart(tmp_path, reference_profile):
    """PowerPoint has no waterfall primitive that survives most templates, so
    every bridge in a banking deck is a stacked column or a table."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    data = CategoryChartData()
    data.categories = ["Opening", "Volume", "Price", "Cost", "Closing"]
    data.add_series("EBITDA movement", (263.0, 45.0, 30.0, 13.0, 400.0))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Pt(36), Pt(120), Pt(600), Pt(280), data
    )
    path = tmp_path / "chart_bridge.pptx"
    presentation.save(str(path))

    findings = findings_for(
        _run(load_deck(path), reference_profile, "CO-007"), "CO-007"
    )
    assert findings
    assert "351" in findings[0].message


def test_co007_needs_more_than_one_step(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "short.pptx",
        [([["Step", "EBITDA movement"], ["Opening", "263"], ["Closing", "400"]], None)],
    )
    assert findings_for(_run(deck, reference_profile, "CO-007"), "CO-007") == []


# --------------------------------------------------------------------------------------
# CO-008 -- units across pages
# --------------------------------------------------------------------------------------


def test_co008_reports_the_same_figure_told_in_two_scales(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "units.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], "Figures in US$ millions"),
            ([["Fiscal year", "Revenue"], ["2025A", "1.908"]], "Figures in US$ billions"),
        ],
    )
    findings = findings_for(_run(deck, reference_profile, "CO-008"), "CO-008")
    assert findings
    assert "millions" in findings[0].message
    assert "billions" in findings[0].message


def test_co008_reports_the_same_figure_told_in_two_currencies(
    tmp_path, reference_profile
):
    deck = _deck(
        tmp_path / "currency.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], "Figures in US$ millions"),
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], "Figures in EUR millions"),
        ],
    )
    findings = findings_for(_run(deck, reference_profile, "CO-008"), "CO-008")
    assert findings
    assert "EUR" in findings[0].message or "USD" in findings[0].message


def test_co008_is_silent_where_the_units_agree(tmp_path, reference_profile):
    deck = _deck(
        tmp_path / "agree_units.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], "Figures in US$ millions"),
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], "Figures in US$ millions"),
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-008"), "CO-008") == []


def test_co008_says_nothing_where_a_unit_is_never_stated(tmp_path, reference_profile):
    """A figure whose scale the deck never gives is not evidence of anything,
    and reporting it would make the rule fire on every unlabelled table."""
    deck = _deck(
        tmp_path / "unstated.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], "Figures in US$ millions"),
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]], None),
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-008"), "CO-008") == []


# --------------------------------------------------------------------------------------
# CO-009 -- as-of dates
# --------------------------------------------------------------------------------------


def _dated(path, second_date):
    return _deck(
        path,
        [
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]],
             "Source: Company management as at 14-September-2026."),
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]],
             f"Source: Company management as at {second_date}."),
        ],
    )


def test_co009_reports_two_as_of_dates_over_one_figure(tmp_path, reference_profile):
    findings = findings_for(
        _run(_dated(tmp_path / "dates.pptx", "30-June-2026"), reference_profile, "CO-009"),
        "CO-009",
    )
    assert findings
    assert "30-June-2026" in findings[0].message
    assert "14-September-2026" in findings[0].message


def test_co009_is_silent_where_the_dates_agree(tmp_path, reference_profile):
    assert (
        findings_for(
            _run(
                _dated(tmp_path / "same.pptx", "14-September-2026"),
                reference_profile,
                "CO-009",
            ),
            "CO-009",
        )
        == []
    )


def test_co009_needs_the_date_to_govern_the_figures(tmp_path, reference_profile):
    """A date in a title is a date. "As at" is a statement about what the
    numbers are true of, and only the second kind is evidence here."""
    deck = _deck(
        tmp_path / "bare_date.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]],
             "Source: Company management as at 14-September-2026."),
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]],
             "Prepared 30-June-2026 for discussion."),
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-009"), "CO-009") == []


def test_co009_says_nothing_where_the_two_slides_share_no_figure(
    tmp_path, reference_profile
):
    """Two dates in a deck are ordinary -- market data at one, financials at
    another. It is two dates over *the same figure* that means one is stale."""
    deck = _deck(
        tmp_path / "unrelated.pptx",
        [
            ([["Fiscal year", "Revenue"], ["2025A", "1,908"]],
             "Source: Company management as at 14-September-2026."),
            ([["Fiscal year", "EBITDA"], ["2024A", "263"]],
             "Source: Company management as at 30-June-2026."),
        ],
    )
    assert findings_for(_run(deck, reference_profile, "CO-009"), "CO-009") == []
