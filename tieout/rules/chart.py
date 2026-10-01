"""Chart rules: whether a reader can actually read the chart.

Charts get their own category because in a banking deck they carry the argument.
A slide's prose can be skimmed; the chart is what the reader looks at, and the
ways a chart fails are not the ways a text box fails.

**These expectations are not learned from a reference deck.** The other
categories derive their expectation from the client's own approved material,
which is right for a palette or a margin: there is no universal correct navy.
There is, though, a universal correct answer to "can the reader tell what these
bars are measured in", and deriving it from one deck would mean a deck that
omits units everywhere teaches the tool that omitting units is the house style.
So the standard here is the craft: what a chart has to carry before a person can
read it without being told.

**Every rule takes multiple pathways to the same fact, and fires only when none
of them leads anywhere.** That is what makes a craft standard safe to assert
against decks built in different styles. A chart's units may be in its axis
title, in its own title, in a caption above it, in the slide's headline, in the
data labels themselves or in a footnote -- six routes, all ordinary, none more
correct than the others. On a real deck the chart carried no title and no axis
title at all, and a naive rule would have reported it twice; the caption above it
read "REVENUE AND EBITDA (EUR M)", which is a house style rather than a defect.
A rule that cannot see that is a rule that gets switched off.

The cost of a false positive is higher here than anywhere else in the tool,
because a chart finding sends someone back to a chart they have already checked.
Where a pathway is ambiguous, these rules stay quiet.
"""

from __future__ import annotations

import re
from typing import ClassVar, Final

from tieout.model.deck import ChartModel, ChartSeries, DeckModel, ShapeModel, SlideModel
from tieout.model.furniture import Furniture
from tieout.profile.schema import Confidence, Profile, Severity
from tieout.rules.base import Finding, Rule, register

# --------------------------------------------------------------------------------------
# Reading the slide around a chart
# --------------------------------------------------------------------------------------

#: How far above a chart a text box can sit and still read as its caption.
#: Generous, because the alternative -- reporting a titled chart as untitled --
#: is the failure that gets a rule disabled.
CAPTION_BAND_PT: Final[float] = 72.0

#: How much of the chart's width a caption must span to be read as belonging to
#: it, rather than being a neighbouring column's heading.
CAPTION_OVERLAP_SHARE: Final[float] = 0.45

#: Ways a quantity's unit or scale gets stated in a banking deck. Deliberately
#: broad: each one only ever suppresses a finding, so a pattern that matches too
#: much costs recall on a rule whose false positives are expensive, and a pattern
#: that matches too little costs nothing but noise.
_UNIT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # A currency, worded or symbolic, with or without a scale: EUR m, $bn, £k.
    re.compile(
        r"(?:EUR|USD|GBP|CHF|JPY|AUD|CAD|SEK|NOK|DKK|\$|€|£|¥)\s*"
        r"(?:m|mm|bn|k|tn|million|billion|thousand)?\b",
        re.IGNORECASE,
    ),
    # A bare scale against a number: "184.2m", "in millions".
    re.compile(r"\b(?:in\s+)?(?:millions?|billions?|thousands?)\b", re.IGNORECASE),
    re.compile(r"\d\s?(?:m|bn|k)\b"),
    # Percentages, multiples, basis points, and the per-unit forms.
    re.compile(r"%|\bpercent\b|\bpp\b|\bbps\b", re.IGNORECASE),
    re.compile(r"\(\s*x\s*\)|\bmultiple\b|\bx\s*EBITDA\b", re.IGNORECASE),
    re.compile(r"\bper\s+\w+|\bunits?\b|\bheadcount\b|\bFTE\b", re.IGNORECASE),
    # An index, which states its own scale by saying it is one.
    re.compile(r"\bindex(?:ed)?\b|\brebased\b", re.IGNORECASE),
)


def _states_a_unit(text: str) -> bool:
    return any(pattern.search(text) for pattern in _UNIT_PATTERNS)


def _caption_candidates(
    slide: SlideModel, chart_shape: ShapeModel, furniture: Furniture
) -> list[ShapeModel]:
    """Text a reader would take as titling this chart.

    A caption sits above the chart and spans a good part of its width. Both
    conditions matter: without the span test, the heading of the column beside
    the chart counts as its title, and a chart genuinely missing one goes
    unreported on every two-column slide in the deck.
    """
    left, right = chart_shape.left_pt, chart_shape.right_pt
    width = max(1.0, right - left)
    out: list[ShapeModel] = []
    for shape in slide.leaf_shapes():
        if shape is chart_shape or not shape.has_text:
            continue
        if furniture.is_furniture(slide.index, shape.ref.uid):
            continue
        overlap = min(right, shape.right_pt) - max(left, shape.left_pt)
        if overlap / width < CAPTION_OVERLAP_SHARE:
            continue
        above = chart_shape.top_pt - shape.bottom_pt
        if -2.0 <= above <= CAPTION_BAND_PT:
            out.append(shape)
    return out


def _slide_text(
    slide: SlideModel, furniture: Furniture, *, exclude: ShapeModel | None = None
) -> str:
    """Everything a reader can see on the slide, chrome excluded.

    ``exclude`` drops one shape, and CH-004 needs it: a chart's own text includes
    its series names, so asking "are the series named anywhere around the chart"
    against text that contains the chart answers yes every time.
    """
    parts = [
        shape.text
        for shape in slide.leaf_shapes()
        if shape.has_text
        and shape is not exclude
        and not furniture.is_furniture(slide.index, shape.ref.uid)
    ]
    return "\n".join(parts)


#: A headline spans the slide. A column heading does not, and `title_shape`
#: falls back to the topmost large text shape, which on a two-column slide is
#: whichever heading happens to be largest.
HEADLINE_WIDTH_SHARE: Final[float] = 0.55


def _headline(slide: SlideModel) -> str:
    """The slide's headline, where it has one a reader would read as such.

    Not ``title_text``: its fallback picks the topmost large text shape, so on a
    slide with a chart in one column and a table in the other, the table's
    heading becomes the slide's title and every untitled chart beside a heading
    goes unreported.
    """
    title = slide.title_shape
    if title is None or not title.text.strip():
        return ""
    if title.placeholder_type in ("title", "ctrTitle"):
        return title.text.strip()
    if title.width_pt >= slide.width_pt * HEADLINE_WIDTH_SHARE:
        return title.text.strip()
    return ""


def _charts(deck: DeckModel, furniture: Furniture) -> list[tuple[SlideModel, ShapeModel]]:
    return [
        (slide, shape)
        for slide in deck.slides
        for shape in slide.leaf_shapes()
        if shape.chart is not None
    ]


def _chart_own_text(chart: ChartModel) -> str:
    return "\n".join(
        part
        for part in (chart.title_text or "", *chart.axis_titles)
        if part
    )


# --------------------------------------------------------------------------------------
# CH-001
# --------------------------------------------------------------------------------------


@register
class ChartNotIdentified(Rule):
    """Reports a chart nothing on the slide names.

    Measures: whether the chart has a title of its own, a caption above it, or a
    slide headline. Any one of the three is enough -- a caption above the plot
    area is at least as common in banking decks as PowerPoint's own chart title,
    and preferring one over the other would be a house-style opinion rather than
    a defect.

    Fires only where all three are absent, which is a chart a reader meets with
    no idea what it shows.
    """

    id: ClassVar[str] = "CH-001"
    category: ClassVar[str] = "chart"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A chart is not named by anything on the slide"

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []
        for slide, shape in _charts(deck, furniture):
            chart = shape.chart
            assert chart is not None
            if chart.has_title and (chart.title_text or "").strip():
                continue
            if _caption_candidates(slide, shape, furniture):
                continue
            if _headline(slide):
                continue
            findings.append(
                self.finding(
                    where=shape.ref,
                    message=(
                        "this chart has no title, no caption above it and no slide "
                        "headline, so nothing on the slide says what it shows"
                    ),
                    profile=profile,
                    measured="no title, caption or headline",
                    expected="a chart title, a caption above it, or a slide headline",
                    remedy="Give the chart a title, or a caption above it",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# CH-002
# --------------------------------------------------------------------------------------


@register
class ChartUnitsNotStated(Rule):
    """Reports a chart whose numbers carry no unit anywhere on the slide.

    The defect this catches is specific and expensive: a revenue chart whose
    bars read 184.2 with nothing on the slide saying whether that is thousands,
    millions or euros. The reader either asks or assumes, and both are bad in a
    document that exists to be trusted.

    Measures: the chart's own title and axis titles, its data-label number
    formats, every caption near it, and the whole of the rest of the slide --
    including the footnote, because "EUR m unless stated" in the source line is
    a perfectly ordinary way to say it. If any of those states a unit, a scale,
    a percentage or a multiple, the chart is readable and nothing is reported.

    Not measured: whether the unit is the *right* one. A chart labelled EUR m
    that plots thousands is wrong in a way no amount of reading the file can
    detect.

    Known limitation: a chart plotting a count of something -- facilities,
    headcount, pallet positions -- may state its unit only in the category
    labels, which this does read, or nowhere at all because the caption makes it
    obvious in prose this cannot parse. That is the direction the rule errs in.
    """

    id: ClassVar[str] = "CH-002"
    category: ClassVar[str] = "chart"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "A chart's units or scale are stated nowhere on the slide"

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []
        for slide, shape in _charts(deck, furniture):
            chart = shape.chart
            assert chart is not None

            # A percentage or currency in the label format is the unit, stated
            # in the most direct place there is.
            formats = " ".join(
                series.label_number_format or "" for series in chart.series
            )
            sources = (
                _chart_own_text(chart),
                formats,
                "\n".join(chart.categories),
                _slide_text(slide, furniture),
            )
            if any(_states_a_unit(text) for text in sources if text):
                continue

            findings.append(
                self.finding(
                    where=shape.ref,
                    message=(
                        "nothing on this slide says what the chart's numbers are "
                        "measured in: not the axis, not a caption, not the footnote"
                    ),
                    profile=profile,
                    measured="no unit, scale or percentage stated",
                    expected="a unit in the axis title, the caption or the footnote",
                    remedy="State the unit, in the axis title or the caption",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# CH-003
# --------------------------------------------------------------------------------------


@register
class TruncatedBarBaseline(Rule):
    """Reports a bar chart whose value axis does not start at zero.

    A bar is read by its length, so cutting the axis at 150 makes a rise from
    160 to 184 look like a doubling. It is the most common way a chart misleads
    without anything on it being false, and the one a reader cannot correct for
    unless they check the axis.

    Restricted to bar and column charts on purpose. A line chart zoomed to its
    range is ordinary practice and often the only legible option, because a line
    is read by its slope rather than by the distance to the axis; applying this
    to every chart type would make it noise.

    Only a *manually pinned* minimum counts. An axis left to scale itself has no
    minimum in the file at all, and PowerPoint's own default for a bar chart is
    zero -- so silence here is genuinely silence, not an assumption.
    """

    id: ClassVar[str] = "CH-003"
    category: ClassVar[str] = "chart"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A bar chart's value axis does not start at zero"

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []
        for _slide, shape in _charts(deck, furniture):
            chart = shape.chart
            assert chart is not None
            minimum = chart.value_axis_minimum
            if not chart.is_baseline_sensitive or minimum == 0:
                continue
            if minimum is None:
                # An automatic axis is scaled by the chart engine from the data
                # at render time, and a bar chart whose values all sit well
                # above zero can be given a non-zero floor without anyone
                # setting one. Nothing in the file says where it will start,
                # so nothing here can. Recorded rather than passed over: a chart
                # that could not be checked must not read as one that was.
                self.note_unchecked(
                    shape.ref,
                    f"this {chart.chart_type} chart's value axis is automatic, so "
                    "whether it starts at zero depends on how the chart engine "
                    "scales the data, which this rule does not model",
                )
                continue
            findings.append(
                self.finding(
                    where=shape.ref,
                    message=(
                        f"this {chart.chart_type} chart's value axis starts at "
                        f"{minimum:g} rather than zero, so the bars exaggerate every "
                        "difference between them"
                    ),
                    profile=profile,
                    measured=f"axis minimum {minimum:g}",
                    expected="axis minimum 0 on a bar chart",
                    remedy="Set the value axis minimum to zero, or use a line chart",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# CH-004
# --------------------------------------------------------------------------------------


@register
class SeriesCannotBeTold(Rule):
    """Reports a chart whose series a reader cannot tell apart.

    Two or more series, no legend, no data labels, and no series name anywhere
    in the text around the chart. The bars are drawn in different colours and
    nothing says which is which.

    Any one of the three routes is enough. A chart whose two series are named in
    the caption -- "Revenue and EBITDA" -- needs no legend, and reporting it
    would be reporting a house style.
    """

    id: ClassVar[str] = "CH-004"
    category: ClassVar[str] = "chart"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A multi-series chart gives no way to tell the series apart"

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []
        for slide, shape in _charts(deck, furniture):
            chart = shape.chart
            assert chart is not None
            named = [s for s in chart.series if (s.name or "").strip()]
            if len(chart.series) < 2 or chart.has_legend:
                continue
            if any(series.has_data_labels for series in chart.series):
                continue
            around = _slide_text(slide, furniture, exclude=shape).casefold()
            if named and all(
                (series.name or "").strip().casefold() in around for series in named
            ):
                continue
            findings.append(
                self.finding(
                    where=shape.ref,
                    message=(
                        f"{len(chart.series)} series, no legend, no data labels and no "
                        "series named in the text around the chart"
                    ),
                    profile=profile,
                    measured=f"{len(chart.series)} series, nothing to identify them",
                    expected="a legend, data labels, or the series named nearby",
                    remedy="Add a legend, or name the series in the caption",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# CH-005
# --------------------------------------------------------------------------------------


@register
class InconsistentLabelPrecision(Rule):
    """Reports one chart whose series label their values to different precision.

    184.2 beside 41 beside 17.60% on the same plot is the chart equivalent of
    TY-006, and it reads as carelessness in exactly the same way. Measured from
    the number formats the series declare, so it is a fact about the file rather
    than a guess from the rendered text.

    Only within one chart. Two charts on a slide may legitimately be formatted
    differently because they plot different things, and comparing across them
    would report every deck that puts a percentage chart beside a currency one.

    A percentage format beside a currency format is not a disagreement: they are
    different quantities, and the decimals belong to the quantity. So the
    comparison is between formats of the same kind.
    """

    id: ClassVar[str] = "CH-005"
    category: ClassVar[str] = "chart"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "Series in one chart label their values to different precision"

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []
        for _slide, shape in _charts(deck, furniture):
            chart = shape.chart
            assert chart is not None
            by_kind: dict[str, dict[str, list[str]]] = {}
            for series in chart.series:
                if not series.has_data_labels:
                    continue
                code = (series.label_number_format or "").strip()
                if not code or code.lower() == "general":
                    # A label with no format of its own, or with "General", shows
                    # each value at whatever precision the source cell holds --
                    # 12 as "12", 12.5 as "12.5", side by side in one series.
                    # Its precision is a property of the data, not of the
                    # chart, and the data is not in the file.
                    self.note_unchecked(
                        shape.ref,
                        f"{series.name or 'a series'} labels its values in the "
                        "source data's own format, so its precision cannot be "
                        "compared with the other series",
                    )
                    continue
                kind = "percentage" if "%" in code else "number"
                by_kind.setdefault(kind, {}).setdefault(
                    _decimals(code), []
                ).append(series.name or "a series")

            for kind, groups in by_kind.items():
                if len(groups) < 2:
                    continue
                listed = "; ".join(
                    f"{places} decimal place{'' if places == '1' else 's'}: "
                    + ", ".join(names)
                    for places, names in sorted(groups.items())
                )
                findings.append(
                    self.finding(
                        where=shape.ref,
                        message=(
                            f"the {kind} series in this chart are labelled to "
                            f"different precision ({listed})"
                        ),
                        profile=profile,
                        measured=f"{len(groups)} precisions in one chart",
                        expected="one precision for every series of a kind",
                        remedy="Give every series of the same kind the same decimals",
                        bbox_pt=shape.bbox_pt,
                    )
                )
        return findings


def _decimals(format_code: str) -> str:
    """Decimal places a number format declares, as a string for grouping."""
    match = re.search(r"\.(0+)", format_code)
    return str(len(match.group(1))) if match else "0"


# --------------------------------------------------------------------------------------
# CH-006
# --------------------------------------------------------------------------------------

#: Two numbers closer than this, relative to their size, are one number written
#: twice. The cache stores the workbook's value as text, so they agree exactly
#: unless they disagree; the slack is only for a round trip through a float.
_SAME_VALUE: Final[float] = 1e-9


def _same(one: float, other: float) -> bool:
    return abs(one - other) <= _SAME_VALUE * max(1.0, abs(one), abs(other))


@register
class ChartCacheDisagreesWithWorkbook(Rule):
    """Reports a chart that draws one value while its own data holds another.

    PLAN.md §5.4. A chart holds its values twice: a cache in the chart part,
    which PowerPoint draws, and the workbook embedded behind it, which
    PowerPoint re-reads the next time anyone opens **Edit Data**. Where the two
    disagree the slide shows one number today, and redraws itself as the other
    the moment someone touches the chart -- after every check has passed, and
    usually after the deck has been signed off. Every tie-out rule reads the
    cache, because that is what the reader sees; none of them can see this.

    Measures: for each series that points at a range in an embedded workbook,
    each point's cached value against the workbook cell it was read from. A
    gap on one side and a number on the other is a disagreement too.

    Not a judgement on which is right. The finding names both, and the remedy
    says what will happen if nothing is done.

    Declines, and says so, where the workbook cannot be read: data linked to a
    file outside the deck, a range spanning several areas, a sheet the
    workbook does not have. Literal values have no workbook, and nothing is
    said about them.

    Known false-positive mode: none seen. A deck whose charts were pasted as
    pictures has no chart to check; one whose workbook was deliberately left
    stale -- an old scenario kept for reference -- will be reported, and is
    the case the remedy's warning is for.
    """

    id: ClassVar[str] = "CH-006"
    category: ClassVar[str] = "chart"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A chart draws values its embedded workbook does not hold"

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for _slide, shape in _charts(deck, self.furniture(deck, profile)):
            chart = shape.chart
            assert chart is not None
            differences: list[str] = []
            for series in chart.series:
                if series.workbook_ref is None:
                    continue
                if series.workbook_values is None:
                    self.note_unchecked(
                        shape.ref,
                        f"{series.name or 'a series'}: {series.workbook_note}",
                    )
                    continue
                differences.extend(self._differences(chart, series))
            if not differences:
                continue
            first = differences[0]
            more = (
                f", and {len(differences) - 1} more point(s)"
                if len(differences) > 1
                else ""
            )
            findings.append(
                self.finding(
                    where=shape.ref,
                    profile=profile,
                    message=(
                        f"the chart draws values its own workbook does not hold: "
                        f"{first}{more}"
                    ),
                    measured=f"{len(differences)} point(s) differ",
                    expected="the drawn values equal the workbook behind them",
                    remedy=(
                        "Open the chart's data (Edit Data) and correct whichever is "
                        "wrong: PowerPoint redraws the chart from the workbook the "
                        "next time it is edited"
                    ),
                    bbox_pt=shape.bbox_pt,
                    signature="\n".join(differences),
                )
            )
        return findings

    @staticmethod
    def _differences(chart: ChartModel, series: ChartSeries) -> list[str]:
        cached = series.values
        stored = series.workbook_values or ()
        name = series.name or "a series"
        reference = series.workbook_ref or ""
        out: list[str] = []
        for position in range(min(len(cached), len(stored))):
            drawn, held = cached[position], stored[position]
            if drawn is None and held is None:
                continue
            if drawn is not None and held is not None and _same(drawn, held):
                continue
            category = (
                chart.categories[position] if position < len(chart.categories) else ""
            )
            out.append(
                f"'{name}'{f' for {category}' if category else ''} draws "
                f"{_plain(drawn)} where {reference} holds {_plain(held)}"
            )
        return out


def _plain(value: float | None) -> str:
    return "nothing" if value is None else f"{value:,.10g}"
