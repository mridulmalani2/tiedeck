"""Internal consistency rules: the same figure, told twice.

These are the checks a banker actually loses sleep over. A logo 4pt out of place
embarrasses; a margin quoted as 15.6% on page 4 and 15.8% on page 12 is the
thing that gets asked about in the room, and the thing nobody catches at 2am on
the fourth turn of a deck.

They are deliberately mechanical and deliberately narrow. Everything here is
arithmetic rather than opinion, it is reproducible, and it needs no profile, no
key and no network. That matters: the same question put to a language model
would cost a key, a network call and reproducibility, and would answer it less
reliably.

These rules no longer walk tables themselves. They read
:mod:`tieout.figures`, which indexes every figure in the deck -- table cells,
chart points and figures stated in prose -- under one answer to "are these two
numbers statements of the same fact?". That is the whole point of the move: the
rule that catches a contradiction between two tables becomes the rule that
catches it between a table and the chart beside it, or between a table and the
headline above it, without a second implementation of what "the same figure"
means. Where this module's first paragraph used to say "everything here is
derived from the deck's own tables", it now says: everything here is derived
from the deck's own figures, wherever they are stated.

The shared limitation, stated once here and repeated in each docstring: two
tables can carry the same label for genuinely different things. "Revenue" under
"FY24" in a group table and in a segment table are different numbers and both
correct. :func:`tieout.figures.comparable` holds the whole of that discipline --
the metric must be specific, the scope must match, the quantity must match, and
the period must not conflict -- so that every rule here inherits it rather than
re-deriving it and getting it slightly wrong. It is still the false-positive
mode to watch.
"""

from __future__ import annotations

import re
from typing import ClassVar, Final

from tieout.figures import (
    Figure,
    build_index,
    comparable,
    normalise_label,
    read_cell_value,
    strip_value_footnote,
)
from tieout.model.deck import DeckModel, ShapeModel, SlideModel, TableModel
from tieout.profile.schema import Confidence, Profile, Severity
from tieout.rules.base import Finding, Rule, cluster_findings, register
from tieout.text import NumberReading, is_numeric_placeholder, parse_number

__all__ = [
    "ContradictoryFigure",
    "ScaleMismatch",
    "TotalDoesNotSum",
    "normalise_label",
    "read_cell_value",
    "strip_value_footnote",
]

#: Row labels that assert their value is the sum of the rows above.
_TOTAL_LABELS: Final[frozenset[str]] = frozenset(
    {"total", "totals", "sum", "aggregate", "grand total", "total group"}
)

#: A total row needs at least this many addends before its arithmetic is worth
#: asserting. Two numbers and a total is as often a subtraction or a comparison.
_MIN_ADDENDS: Final[int] = 3

#: Scale factors that indicate a units error rather than a disagreement.
_SCALE_FACTORS: Final[tuple[float, ...]] = (1000.0, 100.0)

#: How close to an exact scale factor the ratio has to be.
_SCALE_TOLERANCE: Final[float] = 0.02

def _headers(table: TableModel) -> dict[int, str]:
    out: dict[int, str] = {}
    for column in range(table.column_count):
        cell = table.cell(0, column)
        if cell is not None:
            out[column] = normalise_label(cell.text)
    return out


def _format(figure: Figure) -> str:
    """A figure as the deck writes it.

    The raw text, not the parsed value, because a finding that says "1,908" is
    one a reader can find on the slide and "1908.0" is not.
    """
    return figure.raw or figure.reading.raw


def _values(one: Figure, other: Figure) -> tuple[float, float]:
    """The two numbers to compare, in a common scale where one is known.

    Both canonical when both figures know their scale, so "1,908" under a table
    captioned in millions and "1.908" under one captioned in billions compare as
    equal rather than as a contradiction. As written otherwise, which is what
    the rules did before the index existed and is still the only honest reading
    when a deck does not say what its figures are in.

    Mixing the two would be the worst of both: a scale known on one side only
    would compare 1,908,000,000 against 1,908 and report a factor of a million.
    """
    left, right = one.canonical, other.canonical
    if left is not None and right is not None:
        return left, right
    return one.reading.value, other.reading.value


def _describe(figure: Figure) -> str:
    """How a finding names the fact two figures disagree about.

    Built from what the index actually read rather than from a fixed template,
    because the three sources name themselves differently and a message reading
    "'' / 'revenue'" -- which is what a table-shaped template produces for a
    chart point -- is a message nobody can act on.
    """
    parts = [f"'{figure.metric}'"]
    if figure.scope:
        parts.append(f"for '{figure.scope}'")
    if figure.period:
        parts.append(f"in {figure.period}")
    return " ".join(parts)


def _evidence(first: Figure, other: Figure) -> str:
    """Where a weaker anchor came from, appended to the message.

    Silent for two table cells, which is the case the reader already understands
    and the case the message would only get longer for. A chart point or a
    figure read out of a sentence is matched on much thinner evidence, and a
    reader deciding whether to act on the finding needs to see what that
    evidence was.
    """
    parts = [
        f"{figure.metric_from} in {figure.where}"
        for figure in (first, other)
        if figure.source != "table"
    ]
    return f" (matched on {', and '.join(parts)})" if parts else ""


def _spans_two_places(figures: list[Figure]) -> bool:
    """Whether these figures come from at least two distinct shapes.

    The shape, not the slide. Repetition inside one table is a layout artefact
    -- a figure restated in a summary row -- but two tables on one slide stating
    the same figure differently is precisely the contradiction this module
    exists to find, and keying on the slide index hid every one of them.
    """
    return len({figure.place for figure in figures}) >= 2


def _is_scale_of(a: float, b: float) -> bool:
    """Whether two values differ by a clean factor of 100 or 1000.

    Asked of one *pair*. Asking it of a whole group -- min against max -- meant
    that one units error anywhere under a label excused every genuine
    disagreement under it, and CO-001 fell silent on the contradiction it exists
    to report.
    """
    low, high = sorted((abs(a), abs(b)))
    if low == 0:
        return False
    ratio = high / low
    return any(
        abs(ratio - factor) / factor <= _SCALE_TOLERANCE for factor in _SCALE_FACTORS
    )


def _disagreements(
    deck: DeckModel, *, scale: bool
) -> list[tuple[Figure, Figure, str]]:
    """Every pair of figures that claim the same fact and do not agree.

    The baseline is the earliest statement of the figure; every later value that
    differs is measured against it. One pair per distinct value, so a figure
    restated in six places yields one finding per *value*, not per place -- but
    a figure stated three different ways yields two, because a reader who is
    told about one of them has not been told about the other.

    ``scale`` selects which half: CO-002 wants the pairs a clean factor apart,
    CO-001 wants the rest. The third element of each tuple is the confidence
    :func:`tieout.figures.comparable` allowed, which is what stops a finding
    anchored on a sentence claiming the certainty of one anchored on two tables.

    Grouped by period as well as by metric: the index deliberately leaves the
    period out of the grouping key, because a period of ``None`` has to be able
    to match a known one, and a dictionary key cannot do that. So the group is
    gathered loosely and every pair inside it is put to ``comparable``.
    """
    index = build_index(deck)
    out: list[tuple[Figure, Figure, str]] = []
    # Sorted on a key whose third element is ``str | None``. Sorting the raw
    # keys compared None with 'x' the moment a deck stated one metric in two
    # quantities -- "EBITDA of $480m" and "8.7x LTM EBITDA" on one page, which
    # is every deck -- and CO-001 and CO-002 both raised TypeError and checked
    # nothing. The generated decks never state a metric two ways, so 1,651
    # tests passed over it.
    for _, figures in sorted(
        index.grouped().items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2] or ""),
    ):
        if not _spans_two_places(figures):
            continue
        ordered = sorted(figures, key=lambda f: (f.place, f.address))
        for position, baseline in enumerate(ordered):
            base_value_seen: set[float] = set()
            for candidate in ordered[position + 1 :]:
                confidence = comparable(baseline, candidate)
                if confidence is None:
                    continue
                if candidate.place == baseline.place:
                    # Two readings of one figure inside a single shape is that
                    # shape's own layout, not two statements of the same fact.
                    continue
                left, right = _values(baseline, candidate)
                left, right = round(left, 6), round(right, 6)
                if left == right or right in base_value_seen:
                    continue
                if _is_scale_of(left, right) is not scale:
                    continue
                base_value_seen.add(right)
                out.append((baseline, candidate, confidence))
            if out and out[-1][0] is baseline:
                # The earliest statement is the baseline for everything after
                # it, exactly as before. Once it has produced its pairs, a later
                # figure must not become a second baseline for the same group
                # and report the same disagreement from the other end.
                break
    return out


# --------------------------------------------------------------------------------------


@register
class ContradictoryFigure(Rule):
    """Reports the same figure carrying different values in two places.

    Measures: for every metric stated in more than one place -- two tables, a
    table and the chart beside it, a table and the headline above it -- whether
    the values agree. Compared within a quantity, so a percentage is never
    weighed against a multiple, and only where the metric is specific enough to
    identify and the periods do not conflict.

    This is the check the whole module exists for. A margin quoted as 15.6% on
    one page and 15.8% on another is the error that survives every proofread,
    because each page is internally correct and nobody holds both in mind.

    Reading :mod:`tieout.figures` rather than walking tables is what extends it
    past the tables.

    The index's confidence ladder is used as a **gate**, not as a relabelling.
    Anything it will not grade at least ``medium`` is not reported at all; what
    survives is reported at this rule's own declared confidence, weakened by the
    profile's provenance in the usual way. Passing the ladder's grade straight
    into the finding would read better and behave worse: the grade tops out at
    ``high``, which is above what this rule claims for itself, and supplying it
    explicitly is exactly what stops ``Rule.finding`` consulting the profile. A
    finding that quietly outranks the profile it was measured against is not an
    improvement. What the ladder's grade does change is the message, which says
    where a prose- or chart-anchored metric came from so a reader can weigh it.

    Known false-positive mode: two tables can use one label for different
    scopes -- "Revenue / FY24" in a group table and in a segment table are
    different numbers, both right. The period and the scope disambiguate most of
    it, and generic labels are excluded, but a deck with two identically-labelled
    tables covering different entities will report a contradiction that is not
    one. The fix in that case is to label the tables' scopes, which is a
    drafting improvement anyway.
    """

    id: ClassVar[str] = "CO-001"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "The same labelled figure differs between two statements of it"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for first, other, _ in _disagreements(deck, scale=False):
            findings.append(
                self.finding(
                    where=other.ref,
                    profile=profile,
                    provenance_path="consistency",
                    message=(
                        f"{_describe(other)} is {_format(other)} here but "
                        f"{_format(first)} in {first.where}{_evidence(first, other)}"
                    ),
                    measured=_format(other),
                    expected=f"{_format(first)} (slide {first.slide_index})",
                    remedy=(
                        "Reconcile the two figures, or label the scopes so they differ"
                    ),
                    bbox_pt=other.bbox_pt,
                )
            )
        return cluster_findings(findings)


@register
class ScaleMismatch(Rule):
    """Reports the same labelled figure differing by a clean factor of 100 or 1000.

    Measures: the same pair (row label, column header) across tables where the
    values differ by 1000x or 100x within 2%. That is not a disagreement about
    the number, it is the same number in thousands on one page and millions on
    another, and it is worth a different message from CO-001 because the fix is
    different: change the unit note, not the figure.

    Known false-positive mode: a metric that legitimately moves by three orders
    of magnitude between two tables -- a base-case and a stress-case that
    genuinely differ 1000-fold, or a count against a currency amount under the
    same label. Both are rare enough, and wrong enough as drafting, to be worth
    reporting.
    """

    id: ClassVar[str] = "CO-002"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "The same figure appears in two different scales"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for baseline, other, _ in _disagreements(deck, scale=True):
            values = dict(zip((baseline, other), _values(baseline, other), strict=True))
            smaller, larger = sorted(values, key=lambda f: abs(values[f]))
            ratio = abs(values[larger]) / abs(values[smaller])
            findings.append(
                self.finding(
                    where=larger.ref,
                    profile=profile,
                    provenance_path="consistency",
                    message=(
                        f"{_describe(larger)} is {_format(larger)} here and "
                        f"{_format(smaller)} in {smaller.where}, a factor "
                        f"of {ratio:,.0f} apart; one of the two is in the wrong unit"
                    ),
                    measured=_format(larger),
                    expected=f"{_format(smaller)} (slide {smaller.slide_index})",
                    remedy="State both figures in the same scale",
                    bbox_pt=larger.bbox_pt,
                )
            )
        return cluster_findings(findings)


@register
class TotalDoesNotSum(Rule):
    """Reports a row labelled as a total whose value is not the sum above it.

    Measures: for each table row whose label is a total word, whether its value
    in each column equals the sum of that column's other numeric rows, within a
    rounding tolerance of half a unit per addend at the displayed precision.
    Columns holding fewer than three addends are skipped.

    The tolerance is derived, not fixed: five figures each rounded to the nearest
    whole number can legitimately sum to 2.5 away from the rounded total, and a
    rule that demanded exactness would report every correctly-rounded table in
    banking.

    Known false-positive mode: a row labelled "Total" that is not a sum. A
    weighted average, a total that covers entities the rows do not exhaust, or a
    total carried forward from another page. This is why the label has to be a
    total word exactly rather than merely contain one, and why three addends are
    required, but a "Total" row meaning "total company" above non-exhaustive
    segments will be reported.
    """

    id: ClassVar[str] = "CO-003"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "A row labelled as a total does not sum its column"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for slide in deck.slides:
            for shape in slide.tables:
                findings.extend(self._check_table(slide, shape, profile))
        return cluster_findings(findings)

    def _check_table(
        self, slide: SlideModel, shape: ShapeModel, profile: Profile
    ) -> list[Finding]:
        table = shape.table
        if table is None or table.row_count < 3 or table.column_count < 2:
            return []

        total_rows = [
            row
            for row in range(1, table.row_count)
            if (cell := table.cell(row, 0)) is not None
            and _is_total_label(normalise_label(cell.text))
        ]
        if not total_rows:
            return []

        findings: list[Finding] = []
        for total_row in total_rows:
            for column in range(1, table.column_count):
                verdict = self._check_column(table, total_row, column)
                if verdict is None:
                    continue
                if isinstance(verdict, str):
                    self.note_unchecked(shape.ref, verdict)
                    continue
                stated, computed, tolerance, addends = verdict
                label_cell = table.cell(total_row, 0)
                header = table.cell(0, column)
                findings.append(
                    self.finding(
                        where=shape.ref,
                        profile=profile,
                        provenance_path="consistency",
                        message=(
                            f"'{(label_cell.text if label_cell else 'total').strip()}' "
                            f"under '{(header.text if header else '').strip()}' states "
                            f"{stated:,.10g} but the {addends} rows above it sum to "
                            f"{computed:,.10g}"
                        ),
                        measured=f"{stated:,.10g}",
                        expected=f"{computed:,.10g} (+/-{tolerance:,.10g} rounding)",
                        remedy="Correct the total, or the rows it sums",
                        bbox_pt=shape.bbox_pt,
                    )
                )
        del slide
        return findings

    def _check_column(
        self, table: TableModel, total_row: int, column: int
    ) -> tuple[float, float, float, int] | str | None:
        """What this column's total is: a discrepancy, a reason, or nothing.

        Returns a ``(stated, computed, tolerance, addends)`` tuple for a total
        that does not add up, a string when the column could not be read at all
        -- which the caller records as unchecked rather than swallowing -- and
        None when the total is correct or there is nothing to check.

        A total row is accepted if it matches *any* plausible reading of the
        table, because banking tables use several conventions at once: a
        subtotal covers the block above it, a grand total sometimes sums the
        line items and sometimes sums the subtotals, and a running total sums
        everything above. Insisting on one convention reported every table with
        a subtotal in it.

        Only a stated value that matches none of those readings is a finding.
        """
        total_cell = table.cell(total_row, column)
        if total_cell is None or is_numeric_placeholder(total_cell.text):
            return None
        stated = parse_number(total_cell.text)
        if stated is None:
            return None
        if stated.suffix in ("%", "x"):
            # Percentages and multiples do not add. A "Total" over them is a
            # weighted average, and the deck is not showing its working.
            return None

        items, unreadable = self._column_items(table, column, stated.suffix or "")
        if items is None:
            return unreadable

        line_items = [(row, r) for row, r, is_total in items if not is_total]
        subtotals = [
            (row, r) for row, r, is_total in items if is_total and row != total_row
        ]
        previous_total = max(
            (row for row, _, is_total in items if is_total and row < total_row),
            default=0,
        )

        candidates: list[list[tuple[int, NumberReading]]] = [
            # The block since the previous total row.
            [(row, r) for row, r in line_items if previous_total < row < total_row],
            # Everything above this row.
            [(row, r) for row, r in line_items if row < total_row],
            # Every line item in the table.
            line_items,
            # The subtotals above this row.
            [(row, r) for row, r in subtotals if row < total_row],
        ]

        # Acceptance and reporting have different thresholds on purpose. Two
        # addends are enough to *exonerate* a subtotal, but not enough to
        # *accuse* a total: two numbers and a total is as often a subtraction or
        # a comparison. Gating acceptance at three was reporting every table
        # whose first subtotal covered a two-row block.
        best: tuple[float, float, int] | None = None
        for candidate in candidates:
            if len(candidate) < 2:
                continue
            computed = sum(r.value for _, r in candidate)
            tolerance = _rounding_bound([r for _, r in candidate], stated)
            if abs(computed - stated.value) <= tolerance:
                return None  # some reading of the table makes this correct
            if len(candidate) >= _MIN_ADDENDS and (
                best is None or len(candidate) > best[2]
            ):
                best = (computed, tolerance, len(candidate))

        if best is None:
            return None  # nothing large enough to accuse the total with
        computed, tolerance, addends = best
        return (stated.value, computed, tolerance, addends)

    def _column_items(
        self, table: TableModel, column: int, suffix: str
    ) -> tuple[list[tuple[int, NumberReading, bool]] | None, str]:
        """Every usable cell in a column as ``(row, reading, is_total)``.

        Returns ``(None, reason)`` when the column mixes quantities or holds
        prose, because such a column is not a sum whatever a total row claims.

        The reason is returned rather than discarded. A column the rule cannot
        read and a column that adds up correctly used to be indistinguishable
        in the output -- both silent -- so a single annotated cell disabled the
        arithmetic for its whole column and nothing said so. For a pre-send
        check "I could not verify this" and "this is fine" must never look the
        same.
        """
        out: list[tuple[int, NumberReading, bool]] = []
        for row in range(1, table.row_count):
            cell = table.cell(row, column)
            if cell is None or cell.is_merge_continuation:
                continue
            label_cell = table.cell(row, 0)
            is_total = label_cell is not None and _is_total_label(
                normalise_label(label_cell.text)
            )
            if not cell.text or is_numeric_placeholder(cell.text):
                continue
            reading = read_cell_value(cell.text)
            if reading is None:
                return None, (
                    f"row {row} of this column reads {cell.text.strip()!r}, which is "
                    f"not a figure, so the column cannot be added up"
                )
            if (reading.suffix or "") != suffix:
                return None, (
                    f"row {row} of this column is in {reading.suffix!r} where the "
                    f"total is in {suffix!r}, so the column mixes quantities"
                )
            out.append((row, reading, is_total))
        return out, ""


def _rounding_bound(addends: list[NumberReading], stated: NumberReading) -> float:
    """How far a correctly-rounded total may sit from the sum of its addends.

    Each displayed figure stands for a true value within half a unit of its own
    last decimal place, so the bound is the *sum* of those half-units, plus one
    more for the total, which is rounded too.

    Taking the largest precision in the column and applying it to every addend
    was the bug: it is the tightest of the per-figure bounds, so on a column
    mixing one- and two-decimal figures it understated the tolerance by an order
    of magnitude and reported correctly-rounded banking tables as not summing.
    """
    return sum(0.5 * 10.0**-reading.decimals for reading in addends) + 0.5 * (
        10.0**-stated.decimals
    )


def _is_total_label(label: str) -> bool:
    """Whether a row label asserts its value is a sum.

    Matched as a whole label, or as a leading total word followed by a *numeric*
    qualifier naming a period ("Total 2023A-2027E"). Never as a mere prefix:
    "Total addressable market" is a metric, and "Total shareholder return" is a
    percentage, and neither asserts that the rows above it add up.
    """
    if label in _TOTAL_LABELS:
        return True
    head = label.split(" ", 1)
    if len(head) == 2 and head[0] in ("total", "sum", "aggregate"):
        qualifier = head[1]
        # A period qualifier contains a year or a figure. Prose does not.
        return bool(re.search(r"\d", qualifier)) and bool(
            re.fullmatch(r"[\d\sa-z.,%+/()-]*", qualifier)
        )
    return False
