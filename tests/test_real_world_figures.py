"""The tie-out, pointed at a deck written the way a real one is written.

``tests/test_real_world_constructions.py`` did this for the formatting rules and
found that a profile learned from a real twenty-slide deck reported it 168
times, almost all of them the tool disagreeing with itself. The tie-out had
never had the same treatment. Everything in it was verified against the decks
:mod:`tieout.fixtures.generator` builds, and the generator builds what
:mod:`tieout.figures` expects -- which makes that deck the one corpus that
cannot test the index's assumptions. PLAN.md §7 puts this first and §8 says why:
"the third [test to pass for the wrong reason] will be a tie-out rule that
agrees with a bug in the index".

The deck below ties out. Every margin equals its inputs, every multiple equals
its EV over its EBITDA, the segments sum to the group, the bridge carries, the
CAGR is the CAGR, and the prose restates the tables correctly. Pointed at it,
the tie-out produced **a crash and nine defects**, all of them the tool
disagreeing with itself:

==== ======================================================================
CO   what the tool said about a deck with nothing wrong in it
==== ======================================================================
all  CO-001 and CO-002 raised ``TypeError`` and checked nothing, because
     :meth:`FigureIndex.grouped` keys on a quantity that may be ``None`` and
     the caller sorted the keys. Any deck saying "EBITDA of $480m" and
     "8.7x LTM EBITDA" on one page hits it, which is every deck.
001  the Analytics segment P&L contradicted the group P&L, on every row
001  "an enterprise value of $4,180m" was indexed as EBITDA, so EBITDA was
     $4,180m on one slide and $480m on the next
001  "8.0x - 9.5x", one statement of a range, was two multiples disagreeing
001  "GBP 1,510m (US$1,935m)" read as revenue of *minus* 1,935
005  refused every row of the comparables table -- twice over, and the
     second reason was hidden behind the first
006  refused every CAGR in the deck
008  reported the deck's own stated conversion as unit drift
004  a footnote marker put a figure of 1 into the index, labelled EBITDA
==== ======================================================================

Five of those are silent: a refusal, or a figure quietly indexed against
nothing. For a pre-send check "I could not verify this" and "this is fine" must
never look the same, which is why :func:`test_nothing_was_left_unchecked` is a
gate here and not a nicety.

The clean assertion alone would pass on a tie-out that did nothing at all, so
every rule is also given the same deck with one figure changed and has to catch
it. That is the §8 discipline: a purpose-built deck for the defect, and a clean
one the rule must stay silent on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.presentation import Presentation as PresentationPart
from pptx.util import Emu, Pt

from tieout.figures import build_index
from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.rules.base import clear_caches, run_rules

NAVY = RGBColor.from_string("0F2A4A")
GOLD = RGBColor.from_string("C9A227")
GREY = RGBColor.from_string("6B7280")

FOOTER = "Project Marlin  |  Strictly Private and Confidential"

#: Every rule that reads a figure and compares it to another figure.
TIE_OUT_RULES = (
    "CO-001", "CO-002", "CO-003", "CO-004",
    "CO-005", "CO-006", "CO-007", "CO-008", "CO-009",
)


# --------------------------------------------------------------------------------------
# Building a deck in the shape of a real one
# --------------------------------------------------------------------------------------


def _deck() -> PresentationPart:
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    return presentation


def _blank(presentation: PresentationPart):
    return presentation.slides.add_slide(presentation.slide_layouts[6])


def _text(slide, left, top, width, height, text, size=11, colour=NAVY):
    box = slide.shapes.add_textbox(Pt(left), Pt(top), Pt(width), Pt(height))
    frame = box.text_frame
    frame.word_wrap = True
    run = frame.paragraphs[0].add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.name = "Calibri"
    run.font.color.rgb = colour
    return box


def _split_runs(slide, left, top, width, height, pieces, size=11):
    """One figure split across two runs, which is what a stray format change does.

    "$1,1" and "96m" is one number to a reader and two to the XML. PLAN.md §6
    requires it to read as one and to address back to the run holding its
    digits, because that is the run a fix has to rewrite.
    """
    box = slide.shapes.add_textbox(Pt(left), Pt(top), Pt(width), Pt(height))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    for piece in pieces:
        run = paragraph.add_run()
        run.text = piece
        run.font.size = Pt(size)
        run.font.name = "Calibri"
        run.font.color.rgb = NAVY
    return box


def _rect(slide, left, top, width, height, colour):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Pt(left), Pt(top), Pt(width), Pt(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = colour
    shape.line.fill.background()
    return shape


def _chrome(slide, page: int) -> None:
    _text(slide, 835, 27, 166, 22, "HARBOURVIEW PARTNERS", size=6)
    _text(slide, 36, 510, 504, 14, FOOTER, size=8, colour=GREY)
    _text(slide, 900, 510, 24, 14, str(page), size=8, colour=GREY)


def _head(slide, eyebrow: str, headline: str) -> None:
    _rect(slide, 36, 30, 4, 24, GOLD)
    _text(slide, 48, 36, 600, 24, eyebrow, size=14, colour=GOLD)
    _text(slide, 36, 60, 880, 30, headline, size=22)


def _table(slide, left, top, width, rows, name):
    frame = slide.shapes.add_table(
        len(rows), len(rows[0]), Pt(left), Pt(top), Pt(width), Pt(19 * len(rows))
    )
    frame.name = name
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            frame.table.cell(row_index, column_index).text = str(value)
    return frame


def build(path: Path, *, defect: str | None = None) -> Path:
    """An eleven-slide sell-side deck whose every figure ties to every other.

    ``defect`` changes exactly one figure, so each rule can be shown catching
    its own on the same material it must otherwise be silent on.
    """

    def seed(rule_id: str, clean: object, dirty: object) -> object:
        return dirty if defect == rule_id else clean

    presentation = _deck()

    # --- 1. cover ---------------------------------------------------------------
    cover = _blank(presentation)
    _text(cover, 36, 180, 600, 48, "Project Marlin", size=38)
    _text(cover, 36, 240, 600, 24, "Confidential Information Memorandum", size=14)
    _text(cover, 36, 276, 600, 20, "Industrial analytics and sensing")
    _chrome(cover, 1)

    # --- 2. executive summary: prose restating the tables ------------------------
    #     Six constructions, all correct, none of which a generated deck writes:
    #     two periods in one sentence, a metric the sentence names *after* the
    #     figure, a figure split across runs, a footnote marker riding on a
    #     figure, an approximation, and a range.
    summary = _blank(presentation)
    _head(summary, "EXECUTIVE SUMMARY", "Executive Summary")
    _text(summary, 36, 110, 880, 20,
          "Revenue of $1,935m in FY25A, up from $1,352m in FY23A.")
    _text(summary, 36, 140, 880, 20,
          "EBITDA margin of 24.8% in FY25A, an improvement of 80bps on FY24A.")
    _text(summary, 36, 170, 880, 20,
          "An enterprise value of $4,180m implies 8.7x LTM EBITDA.")
    _split_runs(summary, 36, 200, 880, 20,
                ["Analytics revenue reached $1,1", "96m in FY25A."])
    _text(summary, 36, 230, 880, 20, "Group EBITDA of $480m(1) in FY25A.")
    _text(summary, 36, 260, 880, 20,
          "The business serves c.400 industrial sites across four regions.")
    _text(summary, 36, 290, 880, 20,
          "Comparable companies trade at 8.0x - 9.5x LTM EBITDA.")
    _text(summary, 36, 460, 500, 20,
          "(1) Before exceptional items. Figures in US$ millions.",
          size=8, colour=GREY)
    _chrome(summary, 2)

    # --- 3. group P&L ------------------------------------------------------------
    #     312/1,352 = 23.1%, 389/1,624 = 24.0%, 480/1,935 = 24.8%. The corner
    #     cell states the unit, which is where a banking table states it.
    group = _blank(presentation)
    _head(group, "FINANCIAL PERFORMANCE", "Group Financial Performance")
    _table(group, 36, 120, 600, [
        ["$ in millions", "FY23A", "FY24A", "FY25A"],
        ["Revenue", "1,352", "1,624", "1,935"],
        ["Gross profit", "744", "901", "1,084"],
        ["EBITDA", "312", "389", "480*"],
        ["EBITDA margin", "23.1%", "24.0%", seed("CO-004", "24.8%", "26.8%")],
    ], "Group P&L")
    _text(group, 36, 460, 600, 20,
          "* Before exceptional items. Source: Company management as at 30-Jun-25.",
          size=8, colour=GREY)
    _chrome(group, 3)

    # --- 4. revenue by segment: 812 + 540 = 1,352, and so on ---------------------
    segments = _blank(presentation)
    _head(segments, "FINANCIAL PERFORMANCE", "Revenue by Segment")
    #     Three addends, not two: CO-003 skips a column with fewer, on the
    #     stated ground that two rows and a total is not enough evidence that
    #     the total sums them. A real deck has three segments anyway.
    _table(segments, 36, 120, 600, [
        ["Revenue by segment ($m)", "FY23A", "FY24A", "FY25A"],
        ["Analytics", "812", "988", "1,196"],
        ["Sensing", "460", "536", "619"],
        ["Services", "80", "100", "120"],
        # Labelled "Total" and not "Total revenue": CO-003 requires the total
        # word as the whole label, or followed by a period, on the stated ground
        # that "Total addressable market" is a metric and asserts nothing about
        # the rows above it. A segment table's "Total revenue" is a genuine
        # total and is deliberately not read as one -- carried to PLAN.md §9
        # rather than fixed here, because widening it reports every TAM/SAM/SOM
        # slide in banking.
        ["Total", "1,352", "1,624", seed("CO-003", "1,935", "1,965")],
    ], "Segment revenue")
    _chrome(segments, 4)

    # --- 5. the segment P&L, under the group P&L's own row labels ----------------
    #     "Revenue / FY25A" is 1,935 on slide 3 and 1,196 here, and both are
    #     right. CO-001's docstring has always named this false-positive mode
    #     and prescribed "label the tables' scopes"; the corner cell labels it.
    analytics = _blank(presentation)
    _head(analytics, "ANALYTICS", "Analytics Segment Performance")
    _table(analytics, 36, 120, 600, [
        ["Analytics ($m)", "FY23A", "FY24A", "FY25A"],
        ["Revenue", "812", "988", "1,196"],
        ["EBITDA", "203", "252", "316"],
        ["EBITDA margin", "25.0%", "25.5%", "26.4%"],
    ], "Analytics P&L")
    _chrome(analytics, 5)

    # --- 6. trading comparables --------------------------------------------------
    #     Rows are companies, one column is dated and the others are not, and
    #     the last row is a statistic of the rows above rather than one of them.
    comparables = _blank(presentation)
    _head(comparables, "VALUATION", "Trading Comparables")
    _table(comparables, 36, 120, 820, [
        ["Company", "EV ($m)", "LTM EBITDA ($m)", "EV/EBITDA"],
        ["Alpha Corp", "6,420", "712", seed("CO-005", "9.0x", "11.0x")],
        ["Beta Industrial", "3,180", "398", "8.0x"],
        ["Gamma Sensing", "2,050", "226", "9.1x"],
        # 3,180 over 398 is 8.0x; the median *multiple* is 9.0x. Both correct.
        ["Median", "3,180", "398", "9.0x"],
    ], "Comparables")
    _text(comparables, 36, 460, 600, 20,
          "Market data as at 14-Sep-25. Source: FactSet.", size=8, colour=GREY)
    _chrome(comparables, 6)

    # --- 7. valuation summary: a Low/Mid/High table and a range in prose --------
    valuation = _blank(presentation)
    _head(valuation, "VALUATION", "Valuation Summary")
    _table(valuation, 36, 120, 600, [
        ["Valuation ($m)", "Low", "Mid", "High"],
        ["Enterprise value", "3,840", "4,180", "4,560"],
        ["Implied EV/EBITDA", "8.0x", "8.7x", "9.5x"],
        ["Implied equity value", "3,210", "3,550", "3,930"],
    ], "Valuation")
    _text(valuation, 36, 330, 880, 20,
          "The mid case of $4,180m implies 8.7x LTM EBITDA of $480m.")
    _text(valuation, 36, 360, 880, 20,
          "Net debt is n.a. for the pro forma perimeter.")
    _text(valuation, 36, 460, 600, 20,
          "Financial information as at 30-Jun-25.", size=8, colour=GREY)
    _chrome(valuation, 7)

    # --- 8. a revenue bridge: 1,624 + 214 + 128 - 31 = 1,935 ---------------------
    bridge = _blank(presentation)
    _head(bridge, "FINANCIAL PERFORMANCE", "Revenue Bridge FY24A to FY25A")
    _table(bridge, 36, 120, 600, [
        ["Revenue bridge ($m)", "Value"],
        ["FY24A revenue", "1,624"],
        ["Volume", "214"],
        ["Price", "128"],
        ["FX", "(31)"],
        ["FY25A revenue", seed("CO-007", "1,935", "1,955")],
    ], "Bridge")
    #     The bridge restates slide 3's revenue, so the two slides have to be
    #     true as at the same date. Slides 6 and 7 hold market data and
    #     financials at two different dates on purpose, which is ordinary
    #     practice and must stay silent: they share no figure.
    _text(bridge, 36, 460, 600, 20,
          seed("CO-009",
               "Source: Company management as at 30-Jun-25.",
               "Source: Company management as at 31-Dec-24."),
          size=8, colour=GREY)
    _chrome(bridge, 8)

    # --- 9. the same revenues, plotted ------------------------------------------
    chart_slide = _blank(presentation)
    _head(chart_slide, "FINANCIAL PERFORMANCE", "Revenue Development")
    data = CategoryChartData()
    data.categories = ["FY23A", "FY24A", "FY25A"]
    data.add_series("Revenue", (1352.0, 1624.0, seed("CO-001", 1935.0, 1905.0)))
    chart_slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Pt(36), Pt(120), Pt(600), Pt(300), data
    )
    _text(chart_slide, 36, 460, 600, 20,
          "Figures in US$ millions. Source: Company management.",
          size=8, colour=GREY)
    _chrome(chart_slide, 9)

    # --- 10. growth: (1,935/1,352) ** 0.5 - 1 = 19.6% ---------------------------
    growth = _blank(presentation)
    _head(growth, "FINANCIAL PERFORMANCE", "Growth")
    _table(growth, 36, 120, 600, [
        ["Metric", "Value"],
        ["Revenue CAGR FY23A-FY25A", seed("CO-006", "19.6%", "26.0%")],
    ], "Growth")
    _text(growth, 36, 240, 880, 20,
          "Revenue grew at a 19.6% CAGR between FY23A and FY25A.")
    _chrome(growth, 10)

    # --- 11. a second currency, with the conversion stated ----------------------
    currency = _blank(presentation)
    _head(currency, "FINANCIAL PERFORMANCE", "Reported Currency")
    _text(currency, 36, 120, 880, 20,
          "FY25A revenue was GBP 1,510m (US$1,935m) at an average rate of 1.28.")
    if defect in {"CO-002", "CO-008"}:
        # CO-002 wants the same figure a clean factor out; CO-008 wants it
        # correct and told in another scale. One recap table does either.
        _text(currency, 36, 200, 400, 14, "Figures in US$ billions",
              size=8, colour=GREY)
        _table(currency, 36, 220, 400, [
            ["Fiscal year", "Revenue"],
            ["FY25A", "1.935" if defect == "CO-008" else "1,935"],
        ], "Recap")
    _chrome(currency, 11)

    presentation.save(str(path))
    return path


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clean_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build(tmp_path_factory.mktemp("marlin") / "marlin.pptx")


@pytest.fixture(scope="module")
def audited(clean_path: Path):
    clear_caches()
    deck = load_deck(str(clean_path))
    profile = learn_from_decks([deck], "marlin").profile
    return deck, run_rules(deck, profile, include=list(TIE_OUT_RULES))


@pytest.fixture(scope="module")
def index(clean_path: Path):
    clear_caches()
    return build_index(load_deck(str(clean_path)))


# --------------------------------------------------------------------------------------
# The whole point, in three assertions
# --------------------------------------------------------------------------------------


def test_no_tie_out_rule_crashed(audited) -> None:
    """A rule that raised checked nothing, and would make the next test pass.

    :meth:`FigureIndex.grouped` keys on ``(metric, scope, quantity)`` and
    ``quantity`` is ``str | None``; the caller sorted those keys, so a deck
    saying "EBITDA of $480m" and "8.7x LTM EBITDA" on one page compared ``None``
    with ``'x'`` and CO-001 and CO-002 both raised. The reference decks never
    state one metric in two quantities, so nothing in 1,651 tests touched it.
    """
    _, result = audited
    assert not result.failed_rules, [
        f"{skipped.rule_id}: {skipped.reason}" for skipped in result.failed_rules
    ]


def test_a_deck_that_ties_out_is_reported_not_at_all(audited) -> None:
    """Every figure in this deck agrees with every other statement of it."""
    _, result = audited
    reported = [
        f"{finding.rule_id} [{finding.severity}] slide {finding.slide_index}: "
        f"{finding.message}"
        for finding in result.findings
    ]
    assert not reported, "\n".join(reported)


def test_nothing_was_left_unchecked_without_saying_why(audited) -> None:
    """Silence has to be honest.

    Five of the ten defects this deck found were silent rather than reported:
    CO-005 refused all four comparables rows, CO-006 refused every CAGR, and a
    refusal on a deck with nothing wrong in it looks exactly like a pass. The
    one refusal left is the comparables table's median row, which is a
    statistic of the rows above rather than one of them -- and it says so.
    """
    _, result = audited
    reasons = [record.reason for record in result.unchecked]
    assert reasons == [
        "EV/EBITDA is stated for 'median', which is a statistic of the rows "
        "above it rather than one of them, so it is not the ratio of the "
        "figures beside it"
    ], reasons


# --------------------------------------------------------------------------------------
# ...which would all pass on a tie-out that did nothing at all
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", TIE_OUT_RULES)
def test_each_rule_catches_its_own_defect_in_this_deck(
    tmp_path_factory: pytest.TempPathFactory, rule_id: str
) -> None:
    """The same deck with one figure changed, once per rule.

    Without this the three assertions above are satisfied by an index that reads
    nothing and a set of rules that compare nothing, which is precisely the
    failure PLAN.md §8 says has already happened twice.
    """
    path = build(
        tmp_path_factory.mktemp(f"marlin-{rule_id}") / "marlin.pptx", defect=rule_id
    )
    clear_caches()
    deck = load_deck(str(path))
    profile = learn_from_decks([deck], "marlin").profile
    result = run_rules(deck, profile, include=[rule_id])
    assert not result.failed_rules, result.failed_rules
    assert result.findings, f"{rule_id} did not catch its own seeded defect"


# --------------------------------------------------------------------------------------
# The constructions, one test each, so a failure says which one broke
# --------------------------------------------------------------------------------------


def _text_figures(index):
    return [figure for figure in index if figure.source == "text"]


def test_a_figure_split_across_runs_reads_as_one_number(index) -> None:
    """"$1,1" and "96m" is $1,196m, and addresses back to the run with the digits."""
    split = [f for f in _text_figures(index) if f.reading.value == 1196.0]
    assert len(split) == 1, [f.raw for f in _text_figures(index)]
    assert split[0].slide_index == 2
    assert split[0].run is not None


def test_a_footnote_marker_is_not_a_figure(index) -> None:
    """"Group EBITDA of $480m(1)" states one figure, not two.

    The ``(1)`` was read as a figure of 1 labelled ``ebitda`` for the sentence's
    period. It cost no finding only because no real EBITDA is 1; what it cost
    was :meth:`FigureIndex.lookup`, which then saw EBITDA stated two ways and
    refused to compute anything from it.
    """
    assert not [f for f in _text_figures(index) if f.reading.value == 1.0]


def test_the_ends_of_a_range_are_not_two_claims(index) -> None:
    """"8.0x - 9.5x LTM EBITDA" is one statement about a spread.

    Indexed as two multiples, it disagreed with the 8.7x the deck proposes and
    with its own other end. PLAN.md §6: read, and excluded from comparison
    rather than coerced.
    """
    multiples = {f.reading.value for f in _text_figures(index) if f.unit.quantity == "x"}
    assert multiples == {8.7}, multiples


def test_a_figure_is_not_given_a_metric_the_sentence_names_elsewhere(index) -> None:
    """"An enterprise value of $4,180m implies 8.7x LTM EBITDA."

    One metric is named, and taking it for the whole sentence indexed the
    enterprise value as EBITDA. The deck then contradicted itself at ``major``:
    EBITDA was $4,180m on slide 2 and $480m on slide 7.
    """
    ebitda = {
        f.reading.value
        for f in _text_figures(index)
        if f.metric == "ebitda" and f.unit.quantity is None
    }
    assert 4180.0 not in ebitda, ebitda


def test_each_figure_takes_the_period_next_to_it(index) -> None:
    """"Revenue of $1,935m in FY25A, up from $1,352m in FY23A."

    Both figures took the range ``FY2025A..FY2023A``, which agrees with no
    single period, so neither was ever compared with anything -- silently, and
    so did every other comparison sentence in the deck.
    """
    periods = {
        f.reading.value: f.period
        for f in _text_figures(index)
        if f.slide_index == 2 and f.metric == "revenue"
    }
    assert periods[1935.0] == "FY2025A"
    assert periods[1352.0] == "FY2023A"


def test_a_growth_rate_still_takes_the_span_it_is_stated_for(index) -> None:
    """...and "between FY23A and FY25A" is one span, not the nearer of two."""
    cagr = [f for f in _text_figures(index) if f.unit.quantity == "%" and f.slide_index == 10]
    assert [f.period for f in cagr] == ["FY2023A..FY2025A"]


def test_a_segment_table_is_scoped_by_its_own_corner_cell(index) -> None:
    """"Revenue / FY25A" is 1,935 for the group and 1,196 for Analytics."""
    scoped = {
        (f.metric, f.scope, f.period): f.reading.value
        for f in index
        if f.source == "table" and f.metric == "revenue" and f.period == "FY2025A"
    }
    assert scoped[("revenue", "", "FY2025A")] == 1935.0
    assert scoped[("revenue", "analytics", "FY2025A")] == 1196.0


def test_a_comparables_row_is_indexed_one_way_up(index) -> None:
    """EV, LTM EBITDA and EV/EBITDA all belong to the company on their row.

    One of the three column headings carries a period and the other two do not.
    Read as "the columns are the periods", the EBITDA column was filed under the
    *company* as its metric with no scope, while the columns either side of it
    were filed under the company as their scope -- so no row could find its own
    EBITDA and CO-005 refused every multiple in the deck.
    """
    row = {
        f.metric: (f.scope, f.period, f.reading.value)
        for f in index
        if f.slide_index == 6 and f.scope == "alpha corp"
    }
    assert row["ev"] == ("alpha corp", None, 6420.0)
    assert row["ebitda"] == ("alpha corp", "LTM", 712.0)
    assert row["ev ebitda"] == ("alpha corp", None, 9.0)


def test_a_stated_conversion_is_not_unit_drift(index) -> None:
    """"GBP 1,510m (US$1,935m)" is the deck doing what CO-008 asks of it."""
    assert index.conversions
    restated = [f for f in _text_figures(index) if f.unit.currency == "USD"
                and f.slide_index == 11]
    assert [f.reading.value for f in restated] == [1935.0], (
        "the bracketed restatement was read as a negative"
    )


def test_a_table_takes_the_unit_its_corner_cell_states(index) -> None:
    """"$ in millions" sits inside the table, so it is nearer than any caption."""
    cell = next(
        f for f in index
        if f.slide_index == 3 and f.metric == "revenue" and f.period == "FY2025A"
    )
    assert (cell.unit.currency, cell.unit.scale) == ("USD", "m")
