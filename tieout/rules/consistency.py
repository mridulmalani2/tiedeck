"""Internal consistency rules: the same figure, told twice.

These are the checks a banker actually loses sleep over. A logo 4pt out of place
embarrasses; a margin quoted as 15.6% on page 4 and 15.8% on page 12 is the
thing that gets asked about in the room, and the thing nobody catches at 2am on
the fourth turn of a deck.

They are deliberately mechanical and deliberately narrow. Everything here is
derived from the deck's own tables, so a finding is arithmetic rather than
opinion, it is reproducible, and it needs no profile, no key and no network.
That matters: the same question put to a language model would cost a key, a
network call and reproducibility, and would answer it less reliably.

The shared limitation, stated once here and repeated in each docstring: two
tables can carry the same label for genuinely different things. "Revenue" under
"FY24" in a group table and in a segment table are different numbers and both
correct. Every rule below is keyed on the pair (row label, column header)
precisely so that the column disambiguates the scope, and each one requires the
labels to be specific rather than generic. It is still the false-positive mode
to watch.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar, Final

from tieout.model.deck import DeckModel, ShapeModel, SlideModel, TableModel
from tieout.profile.schema import Confidence, Profile, Severity
from tieout.rules.base import Finding, Rule, cluster_findings, register
from tieout.text import NumberReading, is_numeric_placeholder, parse_number

#: Labels too generic to identify a metric across two tables. Keying on one of
#: these invites exactly the false positive described in the module docstring.
_GENERIC_LABELS: Final[frozenset[str]] = frozenset(
    {
        "",
        "total",
        "sum",
        "value",
        "amount",
        "figure",
        "metric",
        "item",
        "description",
        "note",
        "notes",
        "n a",
        "other",
        "various",
    }
)

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

#: A trailing footnote marker: a symbol, or a bracketed number.
#:
#: Deliberately does NOT match a bare trailing number. A digit at the end of a
#: table label is almost always part of the label -- "FY24", "Q1 2026",
#: "Top 10" -- and stripping it silently turned those into "FY", "Q1 20" and
#: "Top", which would have made two unrelated columns compare as one.
_FOOTNOTE_MARKER: Final[re.Pattern[str]] = re.compile(
    "[\\s\u00a0]*(?:[*\u2020\u2021\u00a7\u00b6]+|\\(\\d{1,2}\\)|\\[\\d{1,2}\\])$"
)


#: A footnote marker trailing a *value* rather than a label: "1,234 (2)",
#: "58.1 (a)", "263*". A real table annotates its figures, and a cell the parser
#: cannot read used to disable the arithmetic for its whole column.
#:
#: The lookbehind is what keeps "(5)" a parenthesised negative: a marker is only
#: a marker when something precedes it. The bracketed forms are held to one or
#: two digits, or a single letter, so "(1,234)" and "(2.5)" are never mistaken
#: for one.
_VALUE_FOOTNOTE: Final[re.Pattern[str]] = re.compile(
    r"(?<=\S)[\s\u00a0]*"
    r"(?:[*\u2020\u2021\u00a7\u00b6]+|[(\[](?:\d{1,2}|[a-e])[)\]])$",
    re.IGNORECASE,
)


def strip_value_footnote(text: str) -> str:
    """Remove a trailing footnote marker from a cell's value."""
    return _VALUE_FOOTNOTE.sub("", text.strip()).strip()


def read_cell_value(text: str) -> NumberReading | None:
    """Parse a table cell, tolerating a footnote marker attached to the figure."""
    reading = parse_number(text)
    if reading is not None:
        return reading
    stripped = strip_value_footnote(text)
    return parse_number(stripped) if stripped != text.strip() else None


def normalise_label(text: str) -> str:
    """Fold a row or column label to a comparable key.

    Strips trailing footnote markers and bracketed qualifiers, because "EBITDA"
    and "EBITDA(1)" and "EBITDA " are one label in every deck ever written, and
    treating them as three would make these rules find nothing.

    It does not strip a bare trailing number: see :data:`_FOOTNOTE_MARKER`.
    """
    folded = " ".join(text.split()).casefold()
    folded = folded.split("\n", 1)[0]
    folded = re.sub(r"\((?:[1-9]|1[0-9]|[a-e])\)\s*$", "", folded)
    folded = _FOOTNOTE_MARKER.sub("", folded)
    folded = re.sub(r"[^\w\s%.$-]", " ", folded)
    return " ".join(folded.split()).strip(" :.-")


def _is_specific(label: str) -> bool:
    return bool(label) and label not in _GENERIC_LABELS and len(label) > 2


@dataclass(frozen=True, slots=True)
class Cell:
    """One parsed numeric cell, with enough identity to compare it elsewhere."""

    slide_index: int
    shape: ShapeModel
    table_name: str
    row_label: str
    column_label: str
    reading: NumberReading
    row: int
    column: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.row_label, self.column_label)

    @property
    def where(self) -> str:
        return f"{self.table_name} on slide {self.slide_index}"

    @property
    def comparable(self) -> bool:
        """Whether this cell can be compared with one carrying the same key.

        A percentage and a multiple are not the same quantity even under the same
        label, so the suffix is part of comparability rather than part of the
        key: that way a label carrying both is simply not compared, instead of
        being reported as a contradiction.
        """
        return _is_specific(self.row_label) and _is_specific(self.column_label)


def iter_numeric_cells(deck: DeckModel) -> Iterator[Cell]:
    """Every numeric cell in every table, labelled by its row and column.

    Row labels come from the first column and column labels from the header row,
    which is how every banking table is built. A table with neither is skipped
    rather than guessed at.
    """
    for slide in deck.slides:
        for shape in slide.tables:
            table = shape.table
            if table is None or table.row_count < 2 or table.column_count < 2:
                continue
            headers = _headers(table)
            for row in range(1, table.row_count):
                label_cell = table.cell(row, 0)
                if label_cell is None:
                    continue
                row_label = normalise_label(label_cell.text)
                if not row_label:
                    continue
                for column in range(1, table.column_count):
                    cell = table.cell(row, column)
                    if cell is None or cell.is_merge_continuation:
                        continue
                    text = cell.text
                    if not text or is_numeric_placeholder(text):
                        continue
                    reading = read_cell_value(text)
                    if reading is None:
                        continue
                    yield Cell(
                        slide_index=slide.index,
                        shape=shape,
                        table_name=shape.ref.name,
                        row_label=row_label,
                        column_label=headers.get(column, ""),
                        reading=reading,
                        row=row,
                        column=column,
                    )


def _headers(table: TableModel) -> dict[int, str]:
    out: dict[int, str] = {}
    for column in range(table.column_count):
        cell = table.cell(0, column)
        if cell is not None:
            out[column] = normalise_label(cell.text)
    return out


def _format(reading: NumberReading) -> str:
    return reading.raw


def _grouped_cells(deck: DeckModel) -> dict[tuple[str, str, str], list[Cell]]:
    """Comparable numeric cells, keyed by row label, column header and suffix."""
    grouped: dict[tuple[str, str, str], list[Cell]] = {}
    for cell in iter_numeric_cells(deck):
        if not cell.comparable:
            continue
        suffix = (cell.reading.suffix or "").casefold()
        grouped.setdefault((*cell.key, suffix), []).append(cell)
    return grouped


def _table_of(cell: Cell) -> tuple[int, int]:
    return (cell.slide_index, cell.shape.ref.uid)


def _spans_two_tables(cells: list[Cell]) -> bool:
    """Whether these cells come from at least two distinct tables.

    Keyed on the table, not the slide. Repetition inside one table is a layout
    artefact -- a figure restated in a summary row -- but two tables on one
    slide stating the same figure differently is precisely the contradiction
    this module exists to find, and keying on the slide index hid every one of
    them.
    """
    return len({_table_of(cell) for cell in cells}) >= 2


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
    cells: list[Cell], *, scale: bool
) -> list[tuple[Cell, Cell]]:
    """Each distinct value that differs from the baseline, against the baseline.

    The baseline is the earliest statement of the figure; every later value that
    differs is measured against it. One pair per distinct value, so a figure
    restated in six tables yields one finding per *value*, not per table -- but
    a figure stated three different ways yields two, because a reader who is
    told about one of them has not been told about the other.

    ``scale`` selects which half: CO-002 wants the pairs a clean factor apart,
    CO-001 wants the rest.
    """
    ordered = sorted(cells, key=lambda c: (c.slide_index, c.row, c.column))
    baseline = ordered[0]
    base_value = round(baseline.reading.value, 6)
    out: list[tuple[Cell, Cell]] = []
    seen: set[float] = set()
    for cell in ordered[1:]:
        value = round(cell.reading.value, 6)
        if value == base_value or value in seen:
            continue
        if _table_of(cell) == _table_of(baseline):
            # Two readings of one figure inside a single table is that table's
            # own layout, not two statements of the same fact.
            continue
        if _is_scale_of(value, base_value) is not scale:
            continue
        seen.add(value)
        out.append((baseline, cell))
    return out


# --------------------------------------------------------------------------------------


@register
class ContradictoryFigure(Rule):
    """Reports the same labelled figure carrying different values in two tables.

    Measures: for every pair (row label, column header) that appears in more
    than one table, whether the parsed values agree. Compared within a suffix
    group, so a percentage is never weighed against a multiple, and only where
    both labels are specific enough to identify a metric.

    This is the check the whole module exists for. A margin quoted as 15.6% on
    one page and 15.8% on another is the error that survives every proofread,
    because each page is internally correct and nobody holds both in mind.

    Known false-positive mode: two tables can use one label for different
    scopes -- "Revenue / FY24" in a group table and in a segment table are
    different numbers, both right. Keying on the column header disambiguates
    most of it, and generic labels are excluded, but a deck with two
    identically-headed tables covering different entities will report a
    contradiction that is not one. The fix in that case is to label the tables'
    scopes, which is a drafting improvement anyway.
    """

    id: ClassVar[str] = "CO-001"
    category: ClassVar[str] = "consistency"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "medium"
    summary: ClassVar[str] = "The same labelled figure differs between tables"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for (row_label, column_label, _), cells in sorted(_grouped_cells(deck).items()):
            if not _spans_two_tables(cells):
                continue
            for first, other in _disagreements(cells, scale=False):
                findings.append(
                    self.finding(
                        where=other.shape.ref,
                        profile=profile,
                        provenance_path="consistency",
                        message=(
                            f"'{row_label}' / '{column_label}' is "
                            f"{_format(other.reading)} here but "
                            f"{_format(first.reading)} in {first.where}"
                        ),
                        measured=_format(other.reading),
                        expected=f"{_format(first.reading)} (slide {first.slide_index})",
                        remedy=(
                            "Reconcile the two figures, or label the scopes so they differ"
                        ),
                        bbox_pt=other.shape.bbox_pt,
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
        for (row_label, column_label, _), cells in sorted(_grouped_cells(deck).items()):
            if not _spans_two_tables(cells):
                continue
            for baseline, other in _disagreements(cells, scale=True):
                pair = sorted((baseline, other), key=lambda c: abs(c.reading.value))
                smaller, larger = pair[0], pair[1]
                ratio = abs(larger.reading.value) / abs(smaller.reading.value)
                findings.append(
                    self.finding(
                        where=larger.shape.ref,
                        profile=profile,
                        provenance_path="consistency",
                        message=(
                            f"'{row_label}' / '{column_label}' is "
                            f"{_format(larger.reading)} here and "
                            f"{_format(smaller.reading)} in {smaller.where}, a factor "
                            f"of {ratio:,.0f} apart; one of the two is in the wrong unit"
                        ),
                        measured=_format(larger.reading),
                        expected=(
                            f"{_format(smaller.reading)} (slide {smaller.slide_index})"
                        ),
                        remedy="State both figures in the same scale",
                        bbox_pt=larger.shape.bbox_pt,
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
