"""The figure index: what it reads, and what it refuses to read.

Two halves, and the second is the one that matters.

The first half checks that figures are found — in table cells, in chart caches,
in sentences — with a metric, a period, a unit and an address precise enough to
write a correction back.

The second half checks that they are *not* found, and not matched, everywhere
they should not be. A tie-out that cries wolf is worse than none: it gets
switched off, and the forty-three formatting rules get switched off with it. So
each refusal below is a specific way an earlier version of this module produced
a finding that was not one, kept as a test so it cannot come back:

* a year in a sentence read as a revenue figure;
* a cumulative "Total 2023A-2027E" row read as the 2023A figure and matched
  against a chart's first bar;
* a percentage inheriting "Figures in US$ millions" from the table's footnote
  and coming out as a nine-figure sum;
* two tables on different slides read as one shape, because ``uid`` is unique
  within a slide and not across the deck.
"""

from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Pt

from tieout.figures import (
    Unit,
    build_index,
    comparable,
    is_specific,
    normalise_label,
    parse_period,
    stated_unit,
)
from tieout.model.loader import load_deck

# --------------------------------------------------------------------------------------
# Deck builders
# --------------------------------------------------------------------------------------


def _presentation():
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    return presentation


def _table(slide, rows, *, top=120, name="Table"):
    frame = slide.shapes.add_table(
        len(rows), len(rows[0]), Pt(36), Pt(top), Pt(500), Pt(20 * len(rows))
    )
    frame.name = name
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            frame.table.cell(row_index, column_index).text = str(value)
    return frame


def _text(slide, text, *, top=60):
    box = slide.shapes.add_textbox(Pt(36), Pt(top), Pt(800), Pt(30))
    box.text_frame.text = text
    return box


def _chart(slide, categories, series, *, top=120):
    data = CategoryChartData()
    data.categories = list(categories)
    for name, values in series:
        data.add_series(name, values)
    return slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Pt(36), Pt(top), Pt(500), Pt(280), data
    )


def _save(presentation, path):
    presentation.save(str(path))
    return load_deck(path)


_PROJECTIONS = [
    ["Fiscal year", "Revenue", "EBITDA", "Margin"],
    ["2024A", "1,562", "263", "16.8%"],
    ["2025A", "1,908", "351", "18.4%"],
]


@pytest.fixture
def projections(tmp_path):
    presentation = _presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    _table(slide, _PROJECTIONS)
    _text(slide, "Source: Company management. Figures in US$ millions.", top=420)
    return _save(presentation, tmp_path / "projections.pptx")


# --------------------------------------------------------------------------------------
# Labels and periods
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2025A", "FY2025A"),
        ("2025E", "FY2025E"),
        ("2025", "FY2025"),
        ("FY24", "FY2024"),
        ("FY 2024E", "FY2024E"),
        ("Q1 2026", "Q1-2026"),
        ("1Q24", "Q1-2024"),
        ("H1 2025", "H1-2025"),
        ("LTM", "LTM"),
        ("LTM September 2026", "LTM-FY2026"),
        ("EBITDA", None),
        ("Fiscal year", None),
        ("Company", None),
        ("Top 10", None),
    ],
)
def test_period_parsing(raw, expected):
    assert parse_period(raw) == expected


def test_an_actual_and_an_estimate_are_different_periods():
    """"2025A" and "2025E" are different claims about 2025. A tool that
    conflated them would report every forecast table in banking."""
    assert parse_period("2025A") != parse_period("2025E")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Total 2023A-2027E", "FY2023A..FY2027E"),
        ("2023A-2027E", "FY2023A..FY2027E"),
        ("FY24-FY26", "FY2024..FY2026"),
    ],
)
def test_a_range_of_periods_reads_as_a_range(raw, expected):
    """The cumulative row of the reference deck is labelled "Total
    2023A-2027E". Read as 2023A, its five-year revenue total was matched
    against the single year the chart plots beside it, and the clean deck --
    the one deck in the suite that must be silent -- reported a contradiction
    with itself."""
    assert parse_period(raw) == expected


def test_a_quarter_is_not_also_read_as_its_own_year():
    """"Q1 2026" contains "2026". Without masking each match before the next
    pattern runs, one period reads as two and becomes a range."""
    assert parse_period("Q1 2026") == "Q1-2026"


@pytest.mark.parametrize("label", ["value", "total", "item", "", "of", "n a"])
def test_a_generic_label_identifies_nothing(label):
    assert not is_specific(label)


# --------------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("Figures in US$ millions.", Unit(scale="m", currency="USD")),
        ("EUR m", Unit(scale="m", currency="EUR")),
        ("£bn", Unit(scale="bn", currency="GBP")),
        ("in thousands", Unit(scale="k")),
        ("Source: Company management.", Unit()),
    ],
)
def test_a_caption_states_a_scale(caption, expected):
    assert stated_unit(caption) == expected


def test_a_caption_never_states_a_quantity():
    """"Margin (%)" over a table does not make the revenue column beside it a
    percentage, and a quantity guessed from a caption is how a tie-out becomes
    a nuisance."""
    assert stated_unit("Margin (%) and revenue in US$ m").quantity is None


def test_a_cell_inherits_the_scale_stated_in_the_footnote(projections):
    """The reference deck states its scale in the source line at the foot of
    every slide -- which is what banking decks do. Looking only above the table
    found nothing on any of them."""
    revenue = next(
        f for f in build_index(projections) if f.metric == "revenue" and f.period == "FY2025A"
    )
    assert revenue.unit == Unit(scale="m", currency="USD")
    assert revenue.canonical == pytest.approx(1_908_000_000)


def test_a_percentage_inherits_neither_scale_nor_currency(projections):
    """A margin that inherited "Figures in US$ millions" came out with a
    canonical value of 15,300,000, and any rule comparing canonical values
    would have read every margin in the deck as a nine-figure sum."""
    margin = next(f for f in build_index(projections) if f.metric == "margin")
    assert margin.unit == Unit(quantity="%")
    assert margin.canonical is None


# --------------------------------------------------------------------------------------
# What the index reads
# --------------------------------------------------------------------------------------


def test_a_table_is_read_by_metric_and_period(projections):
    keyed = {(f.period, f.metric): f.raw for f in build_index(projections)}
    assert keyed[("FY2025A", "revenue")] == "1,908"
    assert keyed[("FY2024A", "ebitda")] == "263"


def test_a_table_cell_carries_the_address_a_correction_needs(projections):
    revenue = next(
        f for f in build_index(projections) if f.metric == "revenue" and f.period == "FY2025A"
    )
    assert revenue.source == "table"
    assert (revenue.row, revenue.column) == (2, 1)
    assert revenue.paragraph is None and revenue.series is None
    assert revenue.shape_id > 0


def test_a_table_keyed_by_entity_rather_than_period_puts_the_entity_in_the_scope(
    tmp_path,
):
    """A trading-comparables table names no period at all. The row is the scope
    and the column is the metric, which is how a banker reads it -- and the pair
    is what stops one company's multiple being compared with another's."""
    presentation = _presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    _table(
        slide,
        [
            ["Company", "Enterprise value", "Multiple"],
            ["Calderwood Logistics", "8,420", "9.2x"],
            ["Pemberton Freight", "6,180", "9.6x"],
        ],
    )
    figures = build_index(_save(presentation, tmp_path / "comps.pptx")).figures
    multiples = [f for f in figures if f.metric == "multiple"]
    assert {f.scope for f in multiples} == {"calderwood logistics", "pemberton freight"}
    assert all(f.period is None for f in multiples)
    one, other = multiples
    assert comparable(one, other) is None, "two companies are not one figure"


def test_a_chart_point_is_named_by_its_series_and_category(tmp_path):
    presentation = _presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    _chart(slide, ["2024A", "2025A"], [("Revenue", (1562.0, 1908.0))])
    figures = build_index(_save(presentation, tmp_path / "chart.pptx")).figures
    assert [(f.metric, f.period, f.reading.value) for f in figures] == [
        ("revenue", "FY2024A", 1562.0),
        ("revenue", "FY2025A", 1908.0),
    ]
    assert all(f.source == "chart" for f in figures)
    assert figures[1].series == 0 and figures[1].point == 1


def test_a_chart_category_that_is_not_a_period_becomes_the_scope(tmp_path):
    """A football field's "Low" bar belongs to its methodology. A Low under
    trading comparables is a different fact from a Low under a DCF."""
    presentation = _presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    _chart(
        slide,
        ["Trading comparables", "Discounted cash flow"],
        [("Low", (3210.0, 3400.0))],
    )
    figures = build_index(_save(presentation, tmp_path / "football.pptx")).figures
    assert {f.scope for f in figures} == {
        "trading comparables",
        "discounted cash flow",
    }
    assert comparable(figures[0], figures[1]) is None


def test_a_figure_in_a_sentence_is_read_and_addressed_to_its_run(tmp_path):
    presentation = _presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    _table(first, _PROJECTIONS)
    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    _text(second, "Revenue reached 2,100 in 2025A on continued depot expansion")

    prose = [
        f
        for f in build_index(_save(presentation, tmp_path / "prose.pptx"))
        if f.source == "text"
    ]
    assert len(prose) == 1
    figure = prose[0]
    assert figure.metric == "revenue"
    assert figure.period == "FY2025A"
    assert figure.reading.value == 2100.0
    assert figure.reading.thousands_separator == ","
    assert (figure.paragraph, figure.run) == (0, 0)


# --------------------------------------------------------------------------------------
# What the index refuses
# --------------------------------------------------------------------------------------


def test_a_year_in_a_sentence_is_not_a_figure(tmp_path):
    """"Revenue has compounded at twenty one per cent since 2022" contains one
    number and it is not a revenue figure. Indexing it as one gave the clean
    reference deck a revenue of 2,022 to contradict its own table with."""
    presentation = _presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    _table(first, _PROJECTIONS)
    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    _text(second, "Revenue has compounded at twenty one per cent since 2022")

    assert [f for f in build_index(_save(presentation, tmp_path / "year.pptx"))
            if f.source == "text"] == []


def test_a_sentence_naming_no_known_metric_yields_nothing(tmp_path):
    """Prose is grounded in the deck's own vocabulary. "The team has grown to
    240 people" names no metric any table uses, so it ties to nothing -- which
    is the correct outcome, not a missed one."""
    presentation = _presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    _table(first, _PROJECTIONS)
    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    _text(second, "The team has grown to 240 people across the estate")

    assert [f for f in build_index(_save(presentation, tmp_path / "ungrounded.pptx"))
            if f.source == "text"] == []


def test_a_deck_with_no_tables_indexes_no_prose(tmp_path):
    """Stated as a limit rather than discovered as a bug: with no vocabulary to
    ground it, prose is not indexed at all."""
    presentation = _presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    _text(slide, "Revenue reached 2,100 in 2025A")
    assert build_index(_save(presentation, tmp_path / "bare.pptx")).figures == ()


def test_two_shapes_on_different_slides_are_two_places(tmp_path):
    """``uid`` is unique within a slide, in document order -- so the first table
    on slide 2 and the first table on slide 3 both carry uid 0. Comparing uids
    alone concluded they were the same shape, and every cross-slide
    contradiction in the suite went silent at once."""
    presentation = _presentation()
    for value in ("263", "275"):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        _table(slide, [["Fiscal year", "EBITDA"], ["2025A", value]])

    one, other = build_index(_save(presentation, tmp_path / "two.pptx")).figures
    assert one.uid == other.uid, "the premise of this test: the uids collide"
    assert one.place != other.place
    assert comparable(one, other) == "high"


def test_a_percentage_is_never_compared_with_a_multiple(tmp_path):
    presentation = _presentation()
    for value in ("9.2x", "17.4%"):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        _table(slide, [["Company", "Multiple"], ["Calderwood", value]])
    one, other = build_index(_save(presentation, tmp_path / "quantity.pptx")).figures
    assert comparable(one, other) is None


def test_two_periods_that_are_both_known_and_different_never_match(tmp_path):
    presentation = _presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    _table(slide, _PROJECTIONS)
    figures = build_index(_save(presentation, tmp_path / "periods.pptx")).figures
    revenues = [f for f in figures if f.metric == "revenue"]
    assert comparable(revenues[0], revenues[1]) is None


def test_a_figure_is_never_compared_with_itself(projections):
    for figure in build_index(projections):
        assert comparable(figure, figure) is None


def test_a_prose_figure_with_no_period_on_either_side_is_not_matched(tmp_path):
    """A prose anchor is the weakest evidence there is. Without a period on at
    least one side, "the multiple is 9.6" ties itself to whichever company
    happens to disagree."""
    presentation = _presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    _table(first, [["Company", "Multiple"], ["Calderwood", "9.2x"]])
    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    _text(second, "The multiple implied by the analysis is 9.6x")

    figures = build_index(_save(presentation, tmp_path / "noperiod.pptx")).figures
    prose = [f for f in figures if f.source == "text"]
    table = [f for f in figures if f.source == "table"]
    if prose and table:
        assert comparable(prose[0], table[0]) is None
