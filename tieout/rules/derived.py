"""Derived figures: numbers the deck computes from numbers it also shows.

:mod:`tieout.rules.consistency` catches a figure stated two ways. This module
catches a figure stated *once*, where the deck has already given you everything
needed to check it. A margin is EBITDA over revenue; if the deck prints all
three, the margin is not an assertion, it is arithmetic, and arithmetic can be
wrong in a way that survives every proofread because each number is individually
plausible.

Six rules, and every one of them recomputes from figures the deck itself
supplies:

======  ====================================================================
Rule    Reports
======  ====================================================================
CO-004  A stated margin or ratio that does not equal its inputs
CO-005  A stated multiple (EV/EBITDA, P/E) that does not equal its inputs
CO-006  A stated growth rate or CAGR that does not equal the series it describes
CO-007  A bridge or waterfall whose steps do not carry opening to closing
CO-008  The same metric and period in a different scale or currency
CO-009  More than one as-of date governing the same figures
======  ====================================================================

Three disciplines run through all of them.

**Every finding states its inputs.** "9.2x stated, 9.2x from EV 8,420 (slide 12)
over EBITDA 912 (slide 12)" is a finding someone can check in ten seconds
without opening the deck. A finding that says only "the multiple is wrong" sends
them looking for the working, and they will not find it.

**Refuse rather than guess.** An input that is missing, stated more than one way,
or in a scale that cannot be reconciled stops the check. The reason is recorded
as *unchecked* rather than discarded, because for a pre-send tool "I could not
verify this" and "this is fine" must never look the same. That is the same
argument CO-003 already makes about a column it cannot read.

**The tolerance is derived, not fixed.** Each displayed figure stands for a true
value within half a unit of its own last decimal place, and a ratio inherits the
uncertainty of both its inputs. A correctly-rounded margin is not a defect, and
a rule demanding exactness would report every table in banking. See
:func:`_ratio_bound`.

What is deliberately not here: cross-references ("see page 12") are not checked.
Worth doing, not now.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import ClassVar, Final

from tieout.figures import (
    Figure,
    FigureIndex,
    build_index,
    is_specific,
    normalise_label,
    restate,
    split_period,
    unwritable,
)
from tieout.model.deck import DeckModel, ShapeModel, SlideModel
from tieout.profile.schema import Confidence, Profile, Severity
from tieout.rules.base import Correction, Finding, Rule, cluster_findings, register
from tieout.text import find_dates, parse_number

__all__ = [
    "AsOfDateDrift",
    "BridgeDoesNotCarry",
    "GrowthDoesNotMatch",
    "MarginDoesNotCompute",
    "MultipleDoesNotTie",
    "UnitDrift",
]


# --------------------------------------------------------------------------------------
# The derivations
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Derivation:
    """One thing a deck states that its own figures already determine.

    ``result`` holds every label a deck uses for the answer, ``numerator`` and
    ``denominator`` every label it uses for the inputs. All three are lists
    because the vocabulary is not standardised and a rule that only knew
    "EBITDA margin" would miss the deck that writes "Margin" above an EBITDA
    column -- which is the reference deck, and most decks.
    """

    name: str
    result: tuple[str, ...]
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]
    #: Whether the result is stated as a percentage of the ratio.
    percentage: bool


#: Margins and ratios. ``margin`` bare is read as an EBITDA margin because that
#: is what an unqualified "Margin" column beside an EBITDA column means in a
#: banking deck -- and where it means something else, the recomputation simply
#: fails the tolerance and the finding names both inputs, so the reader can see
#: in one line that the rule read the table the wrong way.
_MARGINS: Final[tuple[Derivation, ...]] = (
    Derivation(
        "EBITDA margin",
        result=("margin", "ebitda margin", "ebitda margin %"),
        numerator=("ebitda", "adjusted ebitda", "adj. ebitda"),
        denominator=("revenue", "sales", "turnover", "net sales", "total revenue"),
        percentage=True,
    ),
    Derivation(
        "gross margin",
        result=("gross margin",),
        numerator=("gross profit",),
        denominator=("revenue", "sales", "turnover", "net sales", "total revenue"),
        percentage=True,
    ),
    Derivation(
        "operating margin",
        result=("operating margin", "ebit margin"),
        numerator=("ebit", "operating profit", "operating income"),
        denominator=("revenue", "sales", "turnover", "net sales", "total revenue"),
        percentage=True,
    ),
    Derivation(
        "net margin",
        result=("net margin", "net income margin"),
        numerator=("net income", "net profit", "profit after tax"),
        denominator=("revenue", "sales", "turnover", "net sales", "total revenue"),
        percentage=True,
    ),
)

#: Multiples. The bare "multiple" of a trading-comparables table is EV/EBITDA
#: for the same reason bare "margin" is an EBITDA margin.
_MULTIPLES: Final[tuple[Derivation, ...]] = (
    Derivation(
        "EV/EBITDA",
        result=("multiple", "ev/ebitda", "ev / ebitda", "ev ebitda", "ev/ebitda multiple"),
        numerator=("enterprise value", "ev", "tev", "implied enterprise value"),
        denominator=("ebitda", "adjusted ebitda", "adj. ebitda"),
        percentage=False,
    ),
    Derivation(
        "EV/Revenue",
        result=("ev/revenue", "ev / revenue", "ev/sales", "ev sales"),
        numerator=("enterprise value", "ev", "tev", "implied enterprise value"),
        denominator=("revenue", "sales", "turnover", "net sales"),
        percentage=False,
    ),
    Derivation(
        "P/E",
        result=("p/e", "pe", "p / e", "price earnings", "p/e multiple"),
        numerator=("market capitalisation", "market capitalization", "market cap", "equity value"),
        denominator=("net income", "net profit", "earnings", "profit after tax"),
        percentage=False,
    ),
)

#: Labels that state a rate of change rather than a level.
_GROWTH_SUFFIXES: Final[tuple[str, ...]] = (
    "cagr",
    "growth",
    "growth rate",
    "growth %",
    "yoy growth",
    "y/y growth",
)


# --------------------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------------------

#: Rows that state a statistic **of** the column rather than a member of it.
#: Every comparables table in banking ends in one, and the arithmetic that holds
#: down each company's row does not hold across it: the median multiple is the
#: median of the multiples, not the median EV over the median EBITDA. On a table
#: whose medians are EV 3,180 and EBITDA 398, recomputing gives 8.0x against a
#: correctly stated 9.0x -- a ``major`` finding, on the one row of the table that
#: cannot be wrong in the way the rule is describing.
#:
#: "Total" and "sum" are deliberately absent. A total row is additive, so its
#: margin really is its own EBITDA over its own revenue, and that is worth
#: checking.
_STATISTIC_ROWS: Final[frozenset[str]] = frozenset(
    {
        "median", "mean", "average", "avg", "weighted average", "simple average",
        "high", "low", "max", "min", "maximum", "minimum", "range",
        "upper quartile", "lower quartile", "25th percentile", "75th percentile",
    }
)


def _is_a_statistic(scope: str) -> bool:
    return scope.strip() in _STATISTIC_ROWS


#: A figure displayed to ``d`` decimals stands for a true value within half a
#: unit of its last place.
def _half_unit(figure: Figure) -> float:
    return 0.5 * 10.0**-figure.reading.decimals


def _pair(numerator: Figure, denominator: Figure) -> tuple[float, float] | str:
    """The two input values in a common scale, or why they have none.

    Scales cancel in a ratio only when they are the same. EBITDA in millions
    over revenue in millions is a margin; EBITDA in millions over revenue in
    billions, compared as written, is a margin a thousand times too big, and a
    rule that reported it would be reporting its own arithmetic.
    """
    if numerator.unit.scale == denominator.unit.scale:
        return numerator.reading.value, denominator.reading.value
    left, right = numerator.canonical, denominator.canonical
    if left is None or right is None:
        return (
            f"{numerator.metric} is in {numerator.unit.scale or 'an unstated scale'} "
            f"and {denominator.metric} in {denominator.unit.scale or 'an unstated scale'}, "
            "so the two cannot be divided"
        )
    return left, right


def _ratio_bound(
    numerator: Figure, denominator: Figure, stated: Figure, *, percentage: bool
) -> float:
    """How far a correctly-rounded ratio may sit from its recomputed value.

    A ratio inherits the uncertainty of both inputs. Moving the numerator by
    half a unit moves the ratio by ``half / denominator``; moving the
    denominator by half a unit moves it by ``value * half / denominator**2``.
    The stated figure is rounded too, so its own half-unit is added.

    Derived rather than fixed for the same reason CO-003's is: a fixed
    tolerance is either so tight that every correctly-rounded table is a defect
    or so loose that a real error fits inside it, and which of the two depends
    on the precision of the deck in front of you.
    """
    values = _pair(numerator, denominator)
    if isinstance(values, str):  # pragma: no cover - guarded by the caller
        return 0.0
    top, bottom = values
    if bottom == 0:
        return 0.0
    scale = 100.0 if percentage else 1.0
    from_numerator = _half_unit(numerator) / abs(bottom)
    from_denominator = abs(top) * _half_unit(denominator) / (bottom * bottom)
    return scale * (from_numerator + from_denominator) + _half_unit(stated)


def _format(figure: Figure) -> str:
    return figure.raw or figure.reading.raw


def _plain(value: float) -> str:
    return f"{value:,.10g}"


def _tol(value: float) -> str:
    """A tolerance, to three significant figures.

    It is a bound, not a measurement: printing it to ten figures invites the
    reader to treat it as one.
    """
    return f"{value:,.3g}"


def _like(value: float, stated: Figure) -> str:
    """``value`` printed to one more decimal than the figure it is checking.

    A recomputed margin rendered at ten significant figures -- "19.01639344%"
    against a stated 24.0% -- buries the comparison the reader came for. One
    extra place says "this is not a rounding difference" without making them
    count digits.
    """
    return f"{value:,.{stated.reading.decimals + 1}f}"


def _fact(figure: Figure) -> str:
    """How a finding names the fact, when the period may be missing.

    A comparables table names its companies and not its years, so "'enterprise
    value' in None" is a real message this produced before the period was made
    optional here rather than interpolated regardless.
    """
    parts = [f"'{figure.metric}'"]
    if figure.scope:
        parts.append(f"for '{figure.scope}'")
    if figure.period:
        parts.append(f"in {figure.period}")
    return " ".join(parts)


def _evidence(figure: Figure) -> str:
    """One input, named the way a reader can find it."""
    return f"{figure.metric} {_format(figure)} (slide {figure.slide_index})"


def _fix(stated: Figure, computed: float) -> Correction:
    """A **Fix it**: the figure is derived, so it has one right answer.

    PLAN.md §5.5 draws the whole line here. A margin that must equal its
    inputs, a multiple that must equal its inputs, a growth rate that must
    equal its series, a bridge's closing figure -- every one of them is
    determined by numbers already on the page, so writing the answer is
    arithmetic and TieOut is not choosing anything. Contrast
    :func:`tieout.rules.consistency._edit`, where two *stated* figures disagree
    and which is right is a judgement about the deal.

    The replacement goes through :func:`tieout.figures.restate`, so it keeps
    the original's decimals, separator, negative style, currency and suffix. A
    fix that corrects 24.0% to 19.0 and drops the per-cent sign has traded an
    arithmetic finding for a typography one.
    """
    return Correction(
        kind="fix",
        source=stated.source,
        shape_id=stated.shape_id,
        uid=stated.uid,
        address=stated.address,
        cell_paragraph=stated.cell_run[0],
        cell_run=stated.cell_run[1],
        current=_format(stated),
        replacement=restate(stated.reading, computed),
        refused=unwritable(stated),
    )


# --------------------------------------------------------------------------------------
# The shared shape of CO-004 and CO-005
# --------------------------------------------------------------------------------------


class _DerivedRatio(Rule):
    """A stated ratio, recomputed from the inputs the deck gives for it.

    CO-004 and CO-005 differ only in their vocabulary and their wording, so the
    work sits here once. Both walk the figure index, find every figure whose
    label names a result they know how to derive, look up the inputs *in the
    same scope and the same period*, and report only where the recomputation
    lands outside the rounding bound.
    """

    derivations: ClassVar[tuple[Derivation, ...]] = ()
    #: The quantity a result of this kind carries, so a plain number under a
    #: "Multiple" heading is not mistaken for a percentage or the reverse.
    quantity: ClassVar[tuple[str | None, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        index = build_index(deck)
        findings: list[Finding] = []
        for stated in index.figures:
            derivation = self._derivation_for(stated)
            if derivation is None:
                continue
            outcome = self._check(index, stated, derivation)
            if outcome is None:
                continue
            if isinstance(outcome, str):
                self.note_unchecked(stated.ref, outcome)
                continue
            computed, tolerance, numerator, denominator = outcome
            findings.append(
                self.finding(
                    where=stated.ref,
                    profile=profile,
                    provenance_path="consistency",
                    message=self.wording(
                        stated, computed, derivation, numerator, denominator
                    ),
                    measured=_format(stated),
                    expected=(
                        f"{_like(computed, stated)} (+/-{_tol(tolerance)} rounding), from "
                        f"{_evidence(numerator)} over {_evidence(denominator)}"
                    ),
                    remedy=self.remedy,
                    correction=_fix(stated, computed),
                    bbox_pt=stated.bbox_pt,
                )
            )
        return cluster_findings(findings)

    remedy: ClassVar[str] = "Correct the ratio, or the inputs it is computed from"

    def wording(
        self,
        stated: Figure,
        computed: float,
        derivation: Derivation,
        numerator: Figure,
        denominator: Figure,
    ) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _derivation_for(self, figure: Figure) -> Derivation | None:
        if figure.unit.quantity not in self.quantity:
            return None
        for derivation in self.derivations:
            if figure.metric in derivation.result:
                return derivation
        return None

    def _check(
        self, index: FigureIndex, stated: Figure, derivation: Derivation
    ) -> tuple[float, float, Figure, Figure] | str | None:
        if _is_a_statistic(stated.scope):
            return (
                f"{derivation.name} is stated for '{stated.scope}', which is a "
                "statistic of the rows above it rather than one of them, so it "
                "is not the ratio of the figures beside it"
            )
        # A multiple that names no period of its own takes its inputs' periods:
        # a comparables row prints an undated EV over an LTM EBITDA and calls
        # the result "EV/EBITDA". Only where the figure itself is undated, and
        # only inside a named scope, so the relaxation cannot reach across to
        # another company or another year.
        undated = stated.period is None
        numerator = index.lookup(
            derivation.numerator,
            scope=stated.scope,
            period=stated.period,
            near=stated,
            period_optional=undated,
        )
        if isinstance(numerator, str):
            return f"{derivation.name} could not be recomputed: {numerator}"
        denominator = index.lookup(
            derivation.denominator,
            scope=stated.scope,
            period=stated.period,
            near=stated,
            period_optional=undated,
        )
        if isinstance(denominator, str):
            return f"{derivation.name} could not be recomputed: {denominator}"

        values = _pair(numerator, denominator)
        if isinstance(values, str):
            return f"{derivation.name} could not be recomputed: {values}"
        top, bottom = values
        if bottom == 0:
            return f"{derivation.name} could not be recomputed: the denominator is zero"

        computed = (top / bottom) * (100.0 if derivation.percentage else 1.0)
        tolerance = _ratio_bound(
            numerator, denominator, stated, percentage=derivation.percentage
        )
        if abs(computed - stated.reading.value) <= tolerance:
            return None
        return computed, tolerance, numerator, denominator


# --------------------------------------------------------------------------------------


@register
class MarginDoesNotCompute(_DerivedRatio):
    """Reports a stated margin that does not equal its own inputs.

    Measures: for every figure labelled as a margin, whether it equals the
    numerator over the denominator that the deck states for the same scope and
    the same period, within a tolerance derived from the displayed precision of
    all three.

    A margin is the figure a reader trusts most and checks least. It is quoted
    in the executive summary, restated in the body, and computed by hand once,
    early, from numbers that later changed.

    Refuses rather than guesses. A margin whose revenue line is missing, stated
    more than one way for the period, or in a scale that cannot be reconciled
    with the numerator's is recorded as unchecked and not reported.

    Known false-positive mode: a margin computed on a base the deck does not
    show. An adjusted EBITDA margin printed beside unadjusted EBITDA, a margin
    on a pro-forma revenue, a segment margin beside group figures. The scope and
    period matching and the same-table preference exclude most of it, and the
    finding names both inputs so the reader can see in one line which numbers
    the rule used -- which is the difference between a false positive that
    wastes ten seconds and one that wastes ten minutes.
    """

    id: ClassVar[str] = "CO-004"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A stated margin does not equal its own inputs"
    requires: ClassVar[tuple[str, ...]] = ()
    derivations: ClassVar[tuple[Derivation, ...]] = _MARGINS
    quantity: ClassVar[tuple[str | None, ...]] = ("%",)
    remedy: ClassVar[str] = "Correct the margin, or the figures it is computed from"

    def wording(
        self,
        stated: Figure,
        computed: float,
        derivation: Derivation,
        numerator: Figure,
        denominator: Figure,
    ) -> str:
        return (
            f"{_format(stated)} stated, {_like(computed, stated)}% from "
            f"{_evidence(numerator)} over {_evidence(denominator)}"
        )


@register
class MultipleDoesNotTie(_DerivedRatio):
    """Reports a stated multiple that does not equal its own inputs.

    Measures: for every figure labelled as a multiple, whether it equals the
    numerator over the denominator the deck states for the same company and the
    same period, within a tolerance derived from the displayed precision of all
    three.

    A comparables table is built by hand from five sets of numbers, and the
    multiple column is the one nobody recomputes because recomputing it is what
    the spreadsheet was for. The spreadsheet is not in the deck.

    Known false-positive mode: a multiple struck on a different measure from the
    one printed beside it -- an EV/EBITDA on next-twelve-months EBITDA in a table
    showing last-twelve-months, or a multiple on a calendarised figure. The
    period matching catches it where the deck labels the periods and misses it
    where the deck does not. The finding names both inputs, so a reader can see
    at once which pair the rule divided.
    """

    id: ClassVar[str] = "CO-005"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A stated multiple does not equal its own inputs"
    requires: ClassVar[tuple[str, ...]] = ()
    derivations: ClassVar[tuple[Derivation, ...]] = _MULTIPLES
    quantity: ClassVar[tuple[str | None, ...]] = ("x",)
    remedy: ClassVar[str] = "Correct the multiple, or the figures it is computed from"

    def wording(
        self,
        stated: Figure,
        computed: float,
        derivation: Derivation,
        numerator: Figure,
        denominator: Figure,
    ) -> str:
        return (
            f"{_format(stated)} stated, {_like(computed, stated)}x from "
            f"{_evidence(numerator)} over {_evidence(denominator)}"
        )


# --------------------------------------------------------------------------------------
# CO-006 -- growth and CAGR
# --------------------------------------------------------------------------------------


def _base_metric(label: str) -> str | None:
    """The thing a growth label describes: "revenue cagr" -> "revenue".

    A bare "CAGR" or "growth" names no base, and a rule that picked one would be
    choosing which series to check against on the strength of nothing.
    """
    for suffix in sorted(_GROWTH_SUFFIXES, key=len, reverse=True):
        if label.endswith(f" {suffix}"):
            base = label[: -len(suffix)].strip()
            return base if is_specific(base) else None
    return None


def _years(period: str | None) -> tuple[str, str] | None:
    """The endpoints of a period range, or None if it is not one."""
    if period is None or ".." not in period:
        return None
    start, end = period.split("..", 1)
    return start, end


def _year_number(period: str) -> int | None:
    match = re.search(r"(\d{4})", period)
    return int(match.group(1)) if match else None


@register
class GrowthDoesNotMatch(Rule):
    """Reports a stated growth rate or CAGR that the series it names does not give.

    Measures: for a figure labelled "<metric> CAGR" or "<metric> growth" over a
    period range, whether it equals the compound annual rate between that
    metric's values at the two endpoints. For a single period, whether it equals
    the year-on-year change from the preceding year.

    A CAGR is quoted in the summary and computed once, from a forecast that has
    since been revised twice. Nobody recomputes it, because recomputing it
    requires finding the series it came from, which is three pages away.

    Refuses rather than guesses. A bare "CAGR" naming no base metric, a range
    whose endpoints the deck does not state, a start value of zero or opposite
    sign -- each is recorded as unchecked rather than reported.

    Known false-positive mode: a CAGR struck on a basis the deck does not show
    -- organic rather than reported, constant-currency, or excluding an
    acquisition. The finding names the two endpoint figures and the number of
    years, so the reader can see the working and recognise their own basis
    difference immediately.
    """

    id: ClassVar[str] = "CO-006"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "A stated growth rate does not match the series it describes"
    requires: ClassVar[tuple[str, ...]] = ()

    #: A CAGR is quoted to one decimal at best and the convention for the
    #: exponent varies (years elapsed against periods shown), so the bound is
    #: wider than a margin's and is stated here rather than buried.
    tolerance_pp: ClassVar[float] = 0.5

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        index = build_index(deck)
        findings: list[Finding] = []
        for stated in index.figures:
            if stated.unit.quantity != "%":
                continue
            base = _base_metric(stated.metric)
            if base is None:
                continue
            outcome = self._check(index, stated, base)
            if outcome is None:
                continue
            if isinstance(outcome, str):
                self.note_unchecked(stated.ref, outcome)
                continue
            computed, start, end, years = outcome
            findings.append(
                self.finding(
                    where=stated.ref,
                    profile=profile,
                    provenance_path="consistency",
                    message=(
                        f"{_format(stated)} stated, {computed:,.1f}% from "
                        f"{_evidence(start)} to {_evidence(end)} over {years} year(s)"
                    ),
                    measured=_format(stated),
                    expected=(
                        f"{computed:,.1f}% (+/-{self.tolerance_pp} rounding), from "
                        f"{_evidence(start)} to {_evidence(end)}"
                    ),
                    remedy="Correct the rate, or the series it is computed from",
                    correction=_fix(stated, computed),
                    bbox_pt=stated.bbox_pt,
                )
            )
        return cluster_findings(findings)

    def _check(
        self, index: FigureIndex, stated: Figure, base: str
    ) -> tuple[float, Figure, Figure, int] | str | None:
        endpoints = _years(stated.period)
        if endpoints is None:
            return None
        first, last = endpoints
        start = index.lookup((base,), scope=stated.scope, period=first, near=stated)
        if isinstance(start, str):
            return f"the {base} growth rate could not be recomputed: {start}"
        end = index.lookup((base,), scope=stated.scope, period=last, near=stated)
        if isinstance(end, str):
            return f"the {base} growth rate could not be recomputed: {end}"

        start_year, end_year = _year_number(first), _year_number(last)
        if start_year is None or end_year is None or end_year <= start_year:
            return (
                f"the {base} growth rate names {stated.period}, whose length "
                "cannot be read, so the rate cannot be recomputed"
            )
        years = end_year - start_year

        values = _pair(end, start)
        if isinstance(values, str):
            return f"the {base} growth rate could not be recomputed: {values}"
        final, initial = values
        if initial <= 0 or final <= 0:
            return (
                f"the {base} series starts or ends at or below zero, so a compound "
                "rate is not defined for it"
            )

        computed = ((final / initial) ** (1.0 / years) - 1.0) * 100.0
        if abs(computed - stated.reading.value) <= self.tolerance_pp:
            return None
        return computed, start, end, years


# --------------------------------------------------------------------------------------
# CO-007 -- bridges and waterfalls
# --------------------------------------------------------------------------------------

#: What a bridge calls its first and last bar. Matched as whole words against
#: the normalised label, so "opening EBITDA" and "FY24 EBITDA (opening)" both
#: count and "open market share" does not.
_OPENING: Final[frozenset[str]] = frozenset(
    {"opening", "open", "start", "starting", "as reported", "reported", "beginning"}
)
_CLOSING: Final[frozenset[str]] = frozenset(
    {"closing", "close", "end", "ending", "final", "pro forma", "adjusted", "bridge to"}
)

#: A bridge with nothing between its ends is not a bridge.
_MIN_STEPS: Final[int] = 2


@dataclass(frozen=True, slots=True)
class _Step:
    """One bar of a bridge, with enough to write its value back.

    ``address`` is ``(row, column)`` in a table and ``(series, point)`` in a
    chart; ``source`` says which, so the closing figure can be corrected where
    it is a cell and refused visibly where it is a bar.
    """

    label: str
    value: float
    decimals: int
    source: str
    address: tuple[int, int]


def _marks(label: str, vocabulary: frozenset[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", label) for word in vocabulary)


@register
class BridgeDoesNotCarry(Rule):
    """Reports a bridge or waterfall whose steps do not carry opening to closing.

    Measures: for a table or chart whose first labelled value names an opening
    and whose last names a closing, whether the opening plus every step between
    equals the closing, within the summed rounding bound of all of them.

    A bridge exists to show that a movement adds up. One that does not is the
    single most embarrassing chart in a deck, because its whole rhetorical
    purpose is arithmetic and the reader will do the arithmetic.

    Refuses rather than guesses. A series with fewer than two steps between its
    ends, or a step that cannot be read as a number, stops the check and is
    recorded as unchecked.

    Known false-positive mode: a bridge with a deliberately hidden step, or one
    whose bars are drawn cumulatively rather than as deltas -- a "waterfall"
    where each bar is a running total rather than a movement. The second is
    detected where the running totals make the sum overshoot by roughly the sum
    of the intermediate levels, and is not separated out; such a chart will be
    reported. Labelling the axis is the fix, and is a drafting improvement.
    """

    id: ClassVar[str] = "CO-007"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A bridge's steps do not carry its opening to its closing"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for slide in deck.slides:
            for shape in slide.all_shapes():
                series = self._series(shape)
                if series is None:
                    continue
                verdict = self._check(series)
                if verdict is None:
                    continue
                if isinstance(verdict, str):
                    self.note_unchecked(shape.ref, verdict)
                    continue
                opening, steps, closing, computed, tolerance = verdict
                findings.append(
                    self.finding(
                        where=shape.ref,
                        profile=profile,
                        provenance_path="consistency",
                        message=(
                            f"the bridge opens at {_plain(opening)} and states "
                            f"{_plain(closing.value)} at the close, but the {steps} "
                            f"step(s) between carry it to {_plain(computed)}"
                        ),
                        measured=_plain(closing.value),
                        expected=f"{_plain(computed)} (+/-{_tol(tolerance)} rounding)",
                        remedy="Correct the closing figure, or the steps between",
                        correction=self._closing_correction(shape, closing, computed),
                        bbox_pt=shape.bbox_pt,
                    )
                )
        del slide
        return cluster_findings(findings)

    def _closing_correction(
        self, shape: ShapeModel, closing: _Step, computed: float
    ) -> Correction | None:
        """A **Fix it** on the closing figure, where it is a cell.

        The closing figure is the one the steps determine; the steps are what a
        person may want to argue with. A bridge drawn as a chart is refused
        visibly, because its bars live in cached data TieOut does not write.
        """
        cell = shape.table.cell(*closing.address) if shape.table is not None else None
        reading = parse_number(cell.text) if cell is not None else None
        if reading is None:
            return None
        return Correction(
            kind="fix",
            source="table",
            shape_id=shape.ref.shape_id,
            uid=shape.ref.uid,
            address=closing.address,
            current=cell.text.strip() if cell is not None else "",
            replacement=restate(reading, computed),
        )

    def _series(self, shape: ShapeModel) -> list[_Step] | None:
        """``(label, value, decimals)`` for a shape that looks like a bridge.

        A bridge is recognised by its ends, not by its chart type: PowerPoint
        has no waterfall primitive that survives a round trip through most
        templates, so every bridge in a banking deck is a stacked column with
        an invisible base, or a table.
        """
        if shape.chart is not None:
            chart = shape.chart
            if not chart.categories or len(chart.series) != 1:
                return None
            values = chart.series[0].values
            plotted: list[_Step] = [
                _Step(normalise_label(category), value, 0, "chart", (0, position))
                for position, category in enumerate(chart.categories)
                if position < len(values) and (value := values[position]) is not None
            ]
            return plotted if self._has_ends(plotted) else None

        table = shape.table
        if table is None or table.row_count < 4 or table.column_count < 2:
            return None
        out: list[_Step] = []
        for row in range(table.row_count):
            label_cell = table.cell(row, 0)
            value_cell = table.cell(row, 1)
            if label_cell is None or value_cell is None:
                continue
            reading = parse_number(value_cell.text)
            if reading is None:
                continue
            out.append(
                _Step(
                    normalise_label(label_cell.text),
                    reading.value,
                    reading.decimals,
                    "table",
                    (row, 1),
                )
            )
        return out if self._has_ends(out) else None

    def _has_ends(self, series: Sequence[_Step]) -> bool:
        if len(series) < _MIN_STEPS + 2:
            return False
        if _marks(series[0].label, _OPENING) and _marks(series[-1].label, _CLOSING):
            return True
        return self._spans_two_periods(series)

    def _spans_two_periods(self, series: Sequence[_Step]) -> bool:
        """A bridge whose ends are named by their periods rather than by a word.

        "FY24A revenue | Volume | Price | FX | FY25A revenue" is how a revenue
        bridge is labelled in practice, and not one label in it is an opening or
        a closing. Recognising a bridge only by those words meant the rule saw
        no bridge at all on a deck whose bridge was drawn the ordinary way, and
        said nothing rather than refusing -- the failure this whole file guards
        against.

        Three conditions, and they are required together because any one alone
        reads a year-by-year history table as a bridge and reports it for not
        summing: the ends name the same metric, they name different periods,
        and **no step between them names a period at all**. A history table
        names one on every row.
        """
        first_metric, first_period = split_period(series[0].label)
        last_metric, last_period = split_period(series[-1].label)
        if first_period is None or last_period is None or first_period == last_period:
            return False
        if not is_specific(first_metric) or first_metric != last_metric:
            return False
        return all(split_period(step.label)[1] is None for step in series[1:-1])

    def _check(
        self, series: list[_Step]
    ) -> tuple[float, int, _Step, float, float] | str | None:
        opening = series[0].value
        closing = series[-1]
        steps = series[1:-1]
        if len(steps) < _MIN_STEPS:
            return "this bridge has fewer than two steps between its ends"
        computed = opening + sum(step.value for step in steps)
        tolerance = sum(0.5 * 10.0**-step.decimals for step in series)
        if abs(computed - closing.value) <= tolerance:
            return None
        return opening, len(steps), closing, computed, tolerance


# --------------------------------------------------------------------------------------
# CO-008 -- units across pages
# --------------------------------------------------------------------------------------


@register
class UnitDrift(Rule):
    """Reports the same metric and period stated in a different scale or currency.

    Measures: for every metric and period the deck states in more than one
    place, whether every statement of it uses the same scale and the same
    currency. Only where both statements name their unit: a figure whose scale
    the deck never states is not evidence of anything.

    CO-002 catches a factor-of-a-thousand error *inside* a table, where the
    figures disagree. This catches the case where they agree perfectly and are
    simply told in different units -- $1,908m on page 6 and $1.908bn on page 9.
    Nothing is arithmetically wrong, and a reader comparing the two pages still
    has to stop and convert, which on the fourth turn of a deck is where the
    error gets introduced.

    Known false-positive mode: a deck that states a summary figure in billions
    and its detail in millions on purpose, which is ordinary practice. This is
    why the rule is ``minor`` and why it names both pages: it is a drafting
    observation, not an accusation, and a deck that means it can suppress it in
    one line.
    """

    id: ClassVar[str] = "CO-008"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "The same figure is stated in two different units"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        index = build_index(deck)
        for figures in index.grouped().values():
            for first, other in _same_fact_pairs(figures):
                reason = self._drift(first, other)
                if reason is None:
                    continue
                if index.reconciles(first, other):
                    # The deck states the conversion -- "GBP 1,510m (US$1,935m)"
                    # -- which is what this rule is asking the drafter to do.
                    # §5.4 scopes CO-008 to the same figure in a different scale
                    # or currency "with no stated conversion"; nothing read the
                    # conversion, so a deck reporting in two currencies and
                    # reconciling them properly was reported for it.
                    continue
                findings.append(
                    self.finding(
                        where=other.ref,
                        profile=profile,
                        provenance_path="consistency",
                        message=(
                            f"{_fact(other)} is {reason} here and "
                            f"{_unit_words(first)} in {first.where}"
                        ),
                        measured=_unit_words(other),
                        expected=f"{_unit_words(first)} (slide {first.slide_index})",
                        remedy="State the same figure in the same unit throughout",
                        bbox_pt=other.bbox_pt,
                    )
                )
        return cluster_findings(findings)

    def _drift(self, first: Figure, other: Figure) -> str | None:
        if (
            first.unit.currency
            and other.unit.currency
            and first.unit.currency != other.unit.currency
        ):
            return _unit_words(other)
        if first.unit.scale and other.unit.scale and first.unit.scale != other.unit.scale:
            return _unit_words(other)
        return None


_SCALE_WORDS: Final[dict[str, str]] = {"k": "thousands", "m": "millions", "bn": "billions"}


def _unit_words(figure: Figure) -> str:
    parts = [figure.unit.currency or "", _SCALE_WORDS.get(figure.unit.scale or "", "")]
    said = " ".join(part for part in parts if part)
    return said or "an unstated unit"


def _same_fact_pairs(figures: list[Figure]) -> list[tuple[Figure, Figure]]:
    """Pairs within a group that state the same fact in two different places.

    The period has to match exactly here rather than merely not conflict, and
    "both sides name no period" counts as a match. Two figures under the same
    metric and the same scope with no period between them are one fact stated
    twice -- a comparables table names its companies and not its years -- and
    the rules built on this are about how a figure is told, where a period that
    merely fails to conflict is not enough to say two statements are of the
    same thing.
    """
    out: list[tuple[Figure, Figure]] = []
    ordered = sorted(figures, key=lambda f: (f.place, f.address))
    for position, first in enumerate(ordered):
        for other in ordered[position + 1 :]:
            if other.period != first.period or other.place == first.place:
                continue
            out.append((first, other))
    return out


# --------------------------------------------------------------------------------------
# CO-009 -- as-of dates
# --------------------------------------------------------------------------------------

#: The phrases that make a date govern the figures rather than merely appear
#: beside them. A date in a title is a date; "as at 14-September-2026" is a
#: statement about what the numbers are true of.
_AS_OF: Final[re.Pattern[str]] = re.compile(
    r"\b(?:as\s+(?:at|of)|dated|as\s+at\s+close|per)\b", re.IGNORECASE
)


def _as_of(slide: SlideModel) -> tuple[date, str] | None:
    """The as-of date governing a slide's figures, where it states one."""
    for shape in slide.leaf_shapes():
        if not shape.has_text:
            continue
        text = shape.text
        if not _AS_OF.search(text):
            continue
        for reading in find_dates(text):
            try:
                parsed = datetime.strptime(reading.raw, reading.format).date()
            except ValueError:  # pragma: no cover - find_dates already parsed it
                continue
            return parsed, reading.raw
    return None


@register
class AsOfDateDrift(Rule):
    """Reports two different as-of dates governing the same figures.

    Measures: for every metric and period the deck states in more than one
    place, whether the slides carrying those statements name the same as-of
    date. Only where both slides state one, and only where the phrasing makes
    the date govern the numbers -- "as at", "as of", "dated" -- rather than
    merely appear near them.

    "As at 14-September" on one page and "as at 30-June" on the next, over the
    same figures, means one of them is stale. The figures themselves will often
    agree, which is exactly why nobody notices: the deck is internally
    consistent and externally out of date.

    Known false-positive mode: a deck that deliberately holds market data at one
    date and financials at another, and restates one figure on both pages. The
    requirement that the two pages state the *same metric and period* keeps this
    narrow, and the finding names both dates so the reader can dismiss it in a
    glance.
    """

    id: ClassVar[str] = "CO-009"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "Two as-of dates govern the same figures"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        dates = {slide.index: _as_of(slide) for slide in deck.slides}
        findings: list[Finding] = []
        for figures in build_index(deck).grouped().values():
            for first, other in _same_fact_pairs(figures):
                one = dates.get(first.slide_index)
                two = dates.get(other.slide_index)
                if one is None or two is None or one[0] == two[0]:
                    continue
                findings.append(
                    self.finding(
                        where=other.ref,
                        profile=profile,
                        provenance_path="consistency",
                        message=(
                            f"{_fact(other)} is stated here as at {two[1]} and on "
                            f"slide {first.slide_index} as at {one[1]}"
                        ),
                        measured=f"as at {two[1]}",
                        expected=f"as at {one[1]} (slide {first.slide_index})",
                        remedy="State one as-of date, or say why the two differ",
                        bbox_pt=other.bbox_pt,
                    )
                )
        return cluster_findings(findings)
