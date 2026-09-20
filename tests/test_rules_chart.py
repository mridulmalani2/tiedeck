"""Chart rule tests.

These rules assert a craft standard rather than deriving one from the client's
reference material, which makes them the rules most able to embarrass the tool:
an expectation nobody agreed to, applied to a deck built in a different house
style, is noise with a rule id attached.

So each rule is tested twice over. Once that it fires on the defect, and once for
*every pathway* that should silence it -- a chart titled by a caption above it
rather than by PowerPoint's own title, units stated in a footnote rather than on
an axis, series named in prose rather than in a legend. The second set is the
one that matters: it is the difference between a rule a banker keeps on and a
rule they turn off after the second false positive.
"""

from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Pt

from tieout.model.loader import load_deck
from tieout.rules.base import clear_caches, run_rules

CHART_RULE_IDS = ("CH-001", "CH-002", "CH-003", "CH-004", "CH-005")


@pytest.fixture(autouse=True)
def _isolate():
    clear_caches()
    yield
    clear_caches()


def _slide_with_chart(
    path,
    *,
    headline=None,
    caption=None,
    footnote=None,
    chart_title=None,
    axis_title=None,
    legend=True,
    labels=True,
    label_formats=None,
    axis_minimum=None,
    series=(("Revenue", (160.0, 184.2)), ("EBITDA", (36.0, 41.2))),
    chart_type=XL_CHART_TYPE.COLUMN_CLUSTERED,
):
    """One slide carrying one chart, with every pathway individually switchable."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])

    # Layout 5 carries a title placeholder; emptying it is how "no headline" is
    # expressed, since a placeholder with no text is not a headline.
    slide.shapes.title.text_frame.text = headline or ""

    if caption:
        box = slide.shapes.add_textbox(Pt(60), Pt(130), Pt(500), Pt(24))
        box.text_frame.text = caption
    if footnote:
        box = slide.shapes.add_textbox(Pt(60), Pt(470), Pt(800), Pt(20))
        box.text_frame.text = footnote

    data = CategoryChartData()
    data.categories = ["FY24A", "FY25E"]
    for name, values in series:
        data.add_series(name, values)

    chart = slide.shapes.add_chart(
        chart_type, Pt(60), Pt(170), Pt(500), Pt(280), data
    ).chart
    chart.has_legend = bool(legend)
    if chart_title:
        chart.has_title = True
        chart.chart_title.text_frame.text = chart_title
    if axis_title:
        chart.value_axis.has_title = True
        chart.value_axis.axis_title.text_frame.text = axis_title
    if axis_minimum is not None:
        chart.value_axis.minimum_scale = axis_minimum

    plot = chart.plots[0]
    plot.has_data_labels = bool(labels)
    if labels:
        plot.data_labels.number_format = "0.0"
        plot.data_labels.number_format_is_linked = False
    for index, code in enumerate(label_formats or []):
        # show_value too: python-pptx writes showVal="0" when only the format is
        # set, which is the file honestly saying this series has no labels.
        plot.series[index].data_labels.show_value = True
        plot.series[index].data_labels.number_format = code
        plot.series[index].data_labels.number_format_is_linked = False

    presentation.save(str(path))
    return load_deck(path)


def _run(deck, profile, rule_id):
    return [
        f
        for f in run_rules(deck, profile, include=[rule_id]).findings
        if f.rule_id == rule_id
    ]


# --------------------------------------------------------------------------------------
# CH-001 -- is the chart named by anything at all
# --------------------------------------------------------------------------------------


def test_ch001_reports_a_chart_nothing_names(tmp_path, reference_profile):
    deck = _slide_with_chart(tmp_path / "anon.pptx")
    assert _run(deck, reference_profile, "CH-001")


@pytest.mark.parametrize(
    ("pathway", "kwargs"),
    [
        ("its own chart title", {"chart_title": "Revenue and EBITDA (EUR m)"}),
        ("a caption above it", {"caption": "REVENUE AND EBITDA (EUR M)"}),
        ("the slide headline", {"headline": "Historical financial performance"}),
    ],
)
def test_ch001_is_silent_when_anything_names_the_chart(
    tmp_path, reference_profile, pathway, kwargs
):
    """A caption above the plot area is at least as common in banking decks as
    PowerPoint's own chart title. Preferring one would be a house-style opinion
    rather than a defect."""
    deck = _slide_with_chart(tmp_path / f"{pathway.replace(' ', '-')}.pptx", **kwargs)
    assert not _run(deck, reference_profile, "CH-001"), pathway


def test_ch001_ignores_a_heading_that_belongs_to_the_next_column(
    tmp_path, reference_profile
):
    """Without the span test, the heading of the column beside the chart counts
    as its title and a chart genuinely missing one goes unreported on every
    two-column slide in the deck."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    # A heading far to the right, over the other column.
    box = slide.shapes.add_textbox(Pt(620), Pt(130), Pt(280), Pt(24))
    box.text_frame.text = "KEY OPERATING METRICS"
    data = CategoryChartData()
    data.categories = ["FY24A", "FY25E"]
    data.add_series("Revenue", (160.0, 184.2))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Pt(60), Pt(170), Pt(500), Pt(280), data
    )
    presentation.save(str(tmp_path / "columns.pptx"))

    deck = load_deck(tmp_path / "columns.pptx")
    assert _run(deck, reference_profile, "CH-001")


# --------------------------------------------------------------------------------------
# CH-002 -- can the reader tell what the numbers mean
# --------------------------------------------------------------------------------------


def test_ch002_reports_numbers_with_no_unit_anywhere(tmp_path, reference_profile):
    """A revenue chart whose bars read 184.2 with nothing saying whether that is
    thousands, millions or euros."""
    deck = _slide_with_chart(
        tmp_path / "unitless.pptx", headline="Financial performance", labels=False
    )
    findings = _run(deck, reference_profile, "CH-002")
    assert findings
    assert "measured in" in findings[0].message


@pytest.mark.parametrize(
    ("pathway", "kwargs"),
    [
        ("an axis title", {"axis_title": "EUR m"}),
        ("the chart title", {"chart_title": "Revenue (EUR m)"}),
        ("a caption", {"caption": "REVENUE AND EBITDA (EUR M)"}),
        ("the slide headline", {"headline": "Revenue growth, USD millions"}),
        ("a footnote", {"footnote": "Source: audited accounts. EUR m unless stated."}),
        ("a currency symbol", {"caption": "Revenue (€m)"}),
        ("percentages", {"caption": "EBITDA margin (%)"}),
        ("a multiple", {"caption": "EV / EBITDA (x)"}),
        ("an index", {"caption": "Share price, rebased to 100"}),
    ],
)
def test_ch002_is_silent_wherever_the_unit_is_stated(
    tmp_path, reference_profile, pathway, kwargs
):
    """Six ordinary routes to the same fact, none more correct than the others.
    A rule that only accepts the axis title reports most of the decks it sees."""
    deck = _slide_with_chart(
        tmp_path / f"{abs(hash(pathway))}.pptx", labels=False, **kwargs
    )
    assert not _run(deck, reference_profile, "CH-002"), pathway


def test_ch002_takes_a_percentage_label_format_as_the_unit(tmp_path, reference_profile):
    """Stated in the most direct place there is: on the numbers themselves."""
    deck = _slide_with_chart(
        tmp_path / "pct.pptx",
        headline="Margin",
        labels=True,
        label_formats=["0.0%", "0.0%"],
    )
    assert not _run(deck, reference_profile, "CH-002")


# --------------------------------------------------------------------------------------
# CH-003 -- the truncated baseline
# --------------------------------------------------------------------------------------


def test_ch003_reports_a_bar_chart_cut_off_above_zero(tmp_path, reference_profile):
    """A rise from 160 to 184 drawn against a baseline of 150 looks like a
    doubling. It is the most common way a chart misleads without anything on it
    being false."""
    deck = _slide_with_chart(
        tmp_path / "truncated.pptx", caption="Revenue (EUR m)", axis_minimum=150
    )
    findings = _run(deck, reference_profile, "CH-003")
    assert findings
    assert "150" in (findings[0].measured or "")


def test_ch003_is_silent_on_a_bar_chart_that_starts_at_zero(tmp_path, reference_profile):
    deck = _slide_with_chart(
        tmp_path / "zeroed.pptx", caption="Revenue (EUR m)", axis_minimum=0
    )
    assert not _run(deck, reference_profile, "CH-003")


def test_ch003_records_an_axis_left_to_scale_itself_as_unchecked(
    tmp_path, reference_profile
):
    """An automatic axis has no minimum in the file at all. It is *not* safe to
    call that zero: the chart engine scales it from the data at render time, and
    a column chart whose values cluster well above zero is given a non-zero
    floor without anyone setting one -- the classic truncated bar, produced
    automatically. The rule cannot tell, so it must not fall silent as if it
    had checked and found nothing."""
    deck = _slide_with_chart(tmp_path / "auto.pptx", caption="Revenue (EUR m)")
    result = run_rules(deck, reference_profile, include=["CH-003"])
    assert not result.findings
    assert result.unchecked, "an automatic axis was passed over in silence"
    assert "automatic" in result.unchecked[0].reason


def test_ch003_leaves_a_line_chart_alone(tmp_path, reference_profile):
    """A line is read by its slope, so zooming to the range is ordinary practice
    and often the only legible option."""
    deck = _slide_with_chart(
        tmp_path / "line.pptx",
        caption="Share price (EUR)",
        axis_minimum=150,
        chart_type=XL_CHART_TYPE.LINE,
    )
    assert not _run(deck, reference_profile, "CH-003")


# --------------------------------------------------------------------------------------
# CH-004 -- can the series be told apart
# --------------------------------------------------------------------------------------


def test_ch004_reports_two_series_with_nothing_to_distinguish_them(
    tmp_path, reference_profile
):
    deck = _slide_with_chart(
        tmp_path / "anonymous-series.pptx",
        headline="Performance (EUR m)",
        legend=False,
        labels=False,
    )
    assert _run(deck, reference_profile, "CH-004")


@pytest.mark.parametrize(
    ("pathway", "kwargs"),
    [
        ("a legend", {"legend": True, "labels": False}),
        ("data labels", {"legend": False, "labels": True}),
        (
            "the series named in the caption",
            {"legend": False, "labels": False, "caption": "Revenue and EBITDA (EUR m)"},
        ),
    ],
)
def test_ch004_is_silent_when_the_series_can_be_identified(
    tmp_path, reference_profile, pathway, kwargs
):
    deck = _slide_with_chart(
        tmp_path / f"{abs(hash(pathway))}-id.pptx",
        headline="Performance (EUR m)",
        **kwargs,
    )
    assert not _run(deck, reference_profile, "CH-004"), pathway


def test_ch004_says_nothing_about_a_single_series_chart(tmp_path, reference_profile):
    """One series needs no legend, and reporting it would be reporting a house
    style rather than a defect."""
    deck = _slide_with_chart(
        tmp_path / "one.pptx",
        caption="Revenue (EUR m)",
        legend=False,
        labels=False,
        series=(("Revenue", (160.0, 184.2)),),
    )
    assert not _run(deck, reference_profile, "CH-004")


def test_a_plot_level_label_setting_counts_for_every_series(tmp_path, reference_profile):
    """PowerPoint writes one dLbls beside the series rather than inside each.
    Reading only the series' own block reported every series of a labelled chart
    as unlabelled, which made CH-004 fire on a chart that was perfectly clear."""
    deck = _slide_with_chart(
        tmp_path / "plot-labels.pptx",
        caption="Revenue and EBITDA (EUR m)",
        legend=False,
        labels=True,
    )
    chart = next(
        shape.chart for shape in deck.slides[0].all_shapes() if shape.chart is not None
    )
    assert all(series.has_data_labels for series in chart.series)
    assert not _run(deck, reference_profile, "CH-004")


# --------------------------------------------------------------------------------------
# CH-005 -- one chart, one precision
# --------------------------------------------------------------------------------------


def test_ch005_reports_series_labelled_to_different_precision(
    tmp_path, reference_profile
):
    deck = _slide_with_chart(
        tmp_path / "precision.pptx",
        caption="Revenue and EBITDA (EUR m)",
        label_formats=["0.0", "0"],
    )
    findings = _run(deck, reference_profile, "CH-005")
    assert findings
    assert "precision" in findings[0].message


def test_ch005_is_silent_when_every_series_agrees(tmp_path, reference_profile):
    deck = _slide_with_chart(
        tmp_path / "agreed.pptx",
        caption="Revenue and EBITDA (EUR m)",
        label_formats=["0.0", "0.0"],
    )
    assert not _run(deck, reference_profile, "CH-005")


def test_ch005_records_a_series_labelled_in_its_source_format_rather_than_guessing(
    tmp_path, reference_profile
):
    """A "General" label shows each value at whatever precision the source cell
    holds, so its precision is a property of the data rather than the chart.
    Comparing it would be a guess; passing it over in silence would let a
    genuinely inconsistent chart read as a checked one."""
    deck = _slide_with_chart(
        tmp_path / "general.pptx",
        caption="Revenue and EBITDA (EUR m)",
        label_formats=["0.0", "General"],
    )
    result = run_rules(deck, reference_profile, include=["CH-005"])
    assert not result.findings
    assert result.unchecked, "a General-formatted series was passed over in silence"
    assert "EBITDA" in result.unchecked[0].reason


def test_ch005_does_not_compare_a_percentage_against_a_currency(
    tmp_path, reference_profile
):
    """Different quantities, and the decimals belong to the quantity. Comparing
    them would report every chart that plots a margin beside a revenue."""
    deck = _slide_with_chart(
        tmp_path / "mixed.pptx",
        caption="Revenue (EUR m) and margin (%)",
        label_formats=["0.0", "0%"],
    )
    assert not _run(deck, reference_profile, "CH-005")


# --------------------------------------------------------------------------------------
# The reference decks
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", CHART_RULE_IDS)
def test_the_chart_rules_are_silent_on_the_clean_deck(
    clean_deck, reference_profile, rule_id
):
    """The burden these carry: a craft standard applied to a deck built in a
    different style is noise with a rule id on it."""
    assert not _run(clean_deck, reference_profile, rule_id)


# --------------------------------------------------------------------------------------
# The values behind the chart
# --------------------------------------------------------------------------------------
#
# Not a rule, but the model these rules and the figure index read. Until this
# landed, ``ChartModel`` carried point counts, categories and label flags and no
# numeric values at all, so a chart contradicting the table beside it could not
# be seen -- PLAN.md section 3's second row.


def test_a_series_carries_the_values_it_plots(tmp_path):
    deck = _slide_with_chart(
        tmp_path / "values.pptx",
        caption="Revenue and EBITDA (EUR m)",
        series=(("Revenue", (160.0, 184.2)), ("EBITDA", (36.0, 41.2))),
    )
    chart = next(
        shape.chart for shape in deck.slides[0].all_shapes() if shape.chart is not None
    )
    revenue, ebitda = chart.series
    assert revenue.values == (160.0, 184.2)
    assert ebitda.values == (36.0, 41.2)


def test_the_values_line_up_with_the_categories(tmp_path):
    """The whole point of reading them: a value has to be attributable to a
    period, or it cannot be compared with the table that states the same one."""
    deck = _slide_with_chart(tmp_path / "aligned.pptx", caption="Revenue (EUR m)")
    chart = next(
        shape.chart for shape in deck.slides[0].all_shapes() if shape.chart is not None
    )
    assert chart.categories == ("FY24A", "FY25E")
    assert len(chart.series[0].values) == len(chart.categories)


def test_a_gap_in_the_series_reads_as_a_gap_and_not_as_zero(tmp_path):
    """A sparse cache omits the points it has no value for.

    Reading the ``c:pt`` elements in document order would shift every later
    point one category to the left, and a tie-out rule built on that would
    accuse a correct chart of contradicting a correct table. ``idx`` is what
    keeps them aligned, and the hole comes back as None rather than 0.0 --
    a missing figure and a figure of zero are different claims.
    """
    from lxml import etree

    from tieout.model.loader import _CHART_NS, load_deck

    path = tmp_path / "sparse.pptx"
    _slide_with_chart(path, caption="Revenue (EUR m)")

    # Reach into the saved package and delete the first point of the series,
    # which is how a chart whose first period has no data is actually stored.
    import shutil
    import zipfile

    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(path) as archive:
        archive.extractall(unpacked)
    chart_part = next(unpacked.rglob("charts/chart*.xml"))
    tree = etree.parse(str(chart_part))
    cache = tree.find(".//c:ser/c:val/c:numRef/c:numCache", _CHART_NS)
    first = cache.find("c:pt[@idx='0']", _CHART_NS)
    cache.remove(first)
    tree.write(str(chart_part), xml_declaration=True, encoding="UTF-8", standalone=True)

    rebuilt = tmp_path / "sparse-rebuilt.pptx"
    with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(unpacked.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(unpacked).as_posix())
    shutil.rmtree(unpacked)

    deck = load_deck(rebuilt)
    chart = next(
        shape.chart for shape in deck.slides[0].all_shapes() if shape.chart is not None
    )
    assert chart.series[0].values == (None, 184.2), (
        "the surviving point must stay under its own category"
    )


def test_a_chart_with_no_cache_reports_no_values_rather_than_guessing(tmp_path):
    """Empty, not zeros. A caller must be able to tell "nothing to compare"
    from "a series of zeros", because only one of those is a defect."""
    import shutil
    import zipfile

    from lxml import etree

    from tieout.model.loader import _CHART_NS, load_deck

    path = tmp_path / "nocache.pptx"
    _slide_with_chart(path, caption="Revenue (EUR m)")
    unpacked = tmp_path / "unpacked-nocache"
    with zipfile.ZipFile(path) as archive:
        archive.extractall(unpacked)
    chart_part = next(unpacked.rglob("charts/chart*.xml"))
    tree = etree.parse(str(chart_part))
    for cache in tree.findall(".//c:numCache", _CHART_NS):
        cache.getparent().remove(cache)
    tree.write(str(chart_part), xml_declaration=True, encoding="UTF-8", standalone=True)

    rebuilt = tmp_path / "nocache-rebuilt.pptx"
    with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(unpacked.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(unpacked).as_posix())
    shutil.rmtree(unpacked)

    deck = load_deck(rebuilt)
    chart = next(
        shape.chart for shape in deck.slides[0].all_shapes() if shape.chart is not None
    )
    assert all(series.values == () for series in chart.series)
