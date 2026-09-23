"""One index of every figure in the deck, and what makes two of them the same.

TieOut checks how a deck looks far better than it checks what it says. Of
forty-six rules, three read a figure and compare it to another, and until this
module existed all three read tables only — so a headline claiming 20% above a
table showing 8% was invisible by construction, and so was a chart contradicting
the table beside it.

The fix is not a fourth place that walks tables. It is **one** answer to the
question every tie-out check has to ask first: *are these two numbers statements
of the same fact?* That question has exactly one hard part, and it is not
arithmetic:

    "Revenue / FY24" in a group table and in a segment table are different
    numbers and both correct.

Everything below exists to avoid saying otherwise. A tie-out that cries wolf is
worse than none, because it will be switched off and the formatting rules will
be switched off with it.

What a figure carries
---------------------

* **the reading** — :class:`tieout.text.NumberReading`, unchanged, because it
  already understands parenthesised negatives, thousands separators, currency
  prefixes and the multiple, percentage and basis-point suffixes banking tables
  use. The primitive was always there; it was only ever fed table cells.
* **where it is** — the slide, the shape's ``uid``, and an address precise
  enough to write the figure back: ``(row, column)`` in a table, ``(paragraph,
  run)`` in text, ``(series, point)`` in a chart. The shape's ``shape_id`` rides
  alongside its ``uid`` because the two do different jobs: ``uid`` is identity
  and the de-duplication key, since ``cNvPr@id`` is not unique in practice,
  while ``retext_fix`` writes by ``shape_id`` and ``(paragraph, run)`` — which
  is exactly the address a text figure already has, so a fix needs no new write
  path.
* **what it is of** — a folded metric label.
* **the period** — FY24, Q1 2026, LTM, 2025A, read from the label, the column
  header or the sentence.
* **the unit** — currency, scale and quantity, including a scale stated once for
  a whole table ("in US$ millions") and inherited by its cells.

Where a metric label comes from, and why prose is grounded
----------------------------------------------------------

A table cell takes its metric from its row label and column header, which is how
every banking table is built and is what the existing rules already key on.

Prose is the interesting one, and the approach here is deliberately narrow: a
figure in a sentence is given a metric **only when the sentence names a metric
the deck's own tables and charts already use**. The alternative — extracting the
surrounding noun phrase and treating whatever comes out as a metric — invents
vocabulary, and a tie-out rule built on invented vocabulary is a false-positive
generator with a rule id. Grounding it in the deck means "Revenue grew to $412m"
ties to the Revenue row it contradicts, and "the team has grown to 240 people"
ties to nothing and is silently not compared.

The cost is stated plainly: **a deck with no tables has no prose figures
indexed.** That is the right trade. The figures worth tying out are the ones the
deck states twice, and a deck that states a figure only in prose has nothing to
tie it to.

The confidence ladder
---------------------

===============================================  ==========
Evidence                                         Confidence
===============================================  ==========
Two table cells, specific label, same period     ``high``
A table cell and a chart point                   ``high``
Anything anchored on prose                       ``medium``
Generic metric                                   not reported
===============================================  ==========

One deviation from the ladder as PLAN.md §5.2 states it, made deliberately and
recorded here rather than left for someone to find. The plan also excludes "no
period on either side" outright. Applied literally that would silence the
comparison CO-001 makes today on a table keyed by *entity* rather than period —
the trading-comparables table, where the row label is a company and the column
header is the metric, and the pair already disambiguates the scope without a
period ever appearing. Imposing the period requirement there removes findings
the tool catches correctly now.

So the period is required exactly where the label alone is not enough to
identify the fact:

* two table cells under an identical (row label, column header) key — the key
  is the disambiguator, and a period is used to confirm rather than to qualify;
* a chart point or a prose figure matched against anything — there the metric
  is matched by name across two different constructions, which is much weaker,
  and a period on at least one side is required, with a conflicting period on
  the other refusing the match.

Air-gapped, like everything in :mod:`tieout`: strings and dataclasses in,
measurements out, no network and no model.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from tieout.model.deck import DeckModel, ShapeModel, ShapeRef, SlideModel
from tieout.text import NumberReading, is_numeric_placeholder, parse_number

__all__ = [
    "Figure",
    "FigureIndex",
    "Unit",
    "build_index",
    "comparable",
    "is_specific",
    "normalise_label",
    "parse_period",
    "read_cell_value",
    "restate",
    "strip_value_footnote",
]

#: Where a figure was read from. Decides how its address is spelled and how much
#: weight a comparison involving it can carry.
Source = Literal["table", "text", "chart"]

#: Labels too generic to identify a metric anywhere. Keying on one of these
#: invites exactly the false positive this module exists to avoid.
GENERIC_LABELS: Final[frozenset[str]] = frozenset(
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

#: A trailing footnote marker on a *label*: a symbol, or a bracketed number.
#:
#: Deliberately does NOT match a bare trailing number. A digit at the end of a
#: table label is almost always part of the label -- "FY24", "Q1 2026",
#: "Top 10" -- and stripping it silently turned those into "FY", "Q1 20" and
#: "Top", which would have made two unrelated columns compare as one.
_FOOTNOTE_MARKER: Final[re.Pattern[str]] = re.compile(
    "[\\s ]*(?:[*†‡§¶]+|\\(\\d{1,2}\\)|\\[\\d{1,2}\\])$"
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
    r"(?<=\S)[\s ]*"
    r"(?:[*†‡§¶]+|[(\[](?:\d{1,2}|[a-e])[)\]])$",
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


def is_specific(label: str) -> bool:
    """Whether a label identifies a metric well enough to compare on."""
    return bool(label) and label not in GENERIC_LABELS and len(label) > 2


# --------------------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------------------
#
# A period is what stops "Revenue 1,908" and "Revenue 2,314" reading as a
# contradiction. Parsed rather than pattern-matched loosely, and folded to a
# canonical spelling, so that "FY24", "FY2024" and "2024A" are one period and
# "FY24" and "Q1 24" are not.

#: Marks a figure as actual, budget, estimate or forecast. Part of the period in
#: a banking deck -- "2025A" and "2025E" are different claims about 2025 and
#: comparing them reports every three-year forecast table ever drawn.
_BASIS: Final[dict[str, str]] = {
    "a": "A", "e": "E", "f": "E", "b": "B", "p": "P",
    "pf": "PF", "act": "A", "est": "E", "bud": "B",
}

_YEAR: Final[str] = r"(?:19|20)\d{2}"

_PERIOD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # FY24, FY2024, FY24A, FY 2024E
    re.compile(rf"\bfy\s?({_YEAR}|\d{{2}})\s?([aefbp]|pf)?\b", re.IGNORECASE),
    # 2024A, 2024E, 2024
    re.compile(rf"\b({_YEAR})\s?([aefbp]|pf)?\b", re.IGNORECASE),
    # Q1 2024, Q1 24, 1Q24
    re.compile(rf"\bq([1-4])\s?({_YEAR}|\d{{2}})\b", re.IGNORECASE),
    re.compile(rf"\b([1-4])q\s?({_YEAR}|\d{{2}})\b", re.IGNORECASE),
    # H1 2024, H1 24
    re.compile(rf"\bh([12])\s?({_YEAR}|\d{{2}})\b", re.IGNORECASE),
)

#: Periods that name a window rather than a date. They carry no year, so two of
#: them are the same period only by name -- which is the deck's own convention
#: and is how a banker reads them.
_RELATIVE: Final[frozenset[str]] = frozenset(
    {"ltm", "ntm", "ttm", "ytd", "mtd", "qtd", "lfy", "nfy", "run-rate", "run rate"}
)

#: The same windows as :func:`parse_period` renders them.
_RELATIVE_CANONICAL: Final[frozenset[str]] = frozenset(
    window.replace(" ", "-").upper() for window in _RELATIVE
)


def _four_digit(year: str) -> str:
    """Two-digit years are this century. A deck dated 1998 is not being tied out."""
    return year if len(year) == 4 else f"20{year}"


def _without_periods(text: str) -> str:
    """``text`` with every period token blanked out."""
    return _scan_periods(text)[1]


def _all_periods(text: str) -> list[str]:
    return _scan_periods(text)[0]


def _scan_periods(text: str) -> tuple[list[str], str]:
    """Every period a label names, in order, and the text with them removed.

    Patterns are applied in priority order and each match is blanked out of the
    working string before the next pattern runs. Without that, "Q1 2026" is seen
    twice -- once as a quarter and once as the bare year inside it -- and a
    label naming one period reads as naming two.
    """
    working = " ".join(text.split()).casefold()
    if not working:
        return [], ""

    found: list[tuple[int, str]] = []

    def take(pattern: re.Pattern[str], render: object) -> None:
        nonlocal working
        while True:
            match = pattern.search(working)
            if match is None:
                return
            found.append((match.start(), render(match)))  # type: ignore[operator]
            working = (
                working[: match.start()]
                + " " * (match.end() - match.start())
                + working[match.end() :]
            )

    for window in sorted(_RELATIVE):
        canonical = window.replace(" ", "-").upper()
        take(re.compile(rf"\b{re.escape(window)}\b"), lambda _m, w=canonical: w)

    def _quarter(match: re.Match[str]) -> str:
        first, second = match.groups()
        number, year = (first, second) if len(first) == 1 else (second, first)
        return f"Q{number}-{_four_digit(year)}"

    take(_PERIOD_PATTERNS[2], _quarter)
    take(_PERIOD_PATTERNS[3], _quarter)
    take(
        _PERIOD_PATTERNS[4],
        lambda m: f"H{m.group(1)}-{_four_digit(m.group(2))}",
    )
    take(
        _PERIOD_PATTERNS[0],
        lambda m: f"FY{_four_digit(m.group(1))}{_BASIS.get(m.group(2) or '', '')}",
    )
    take(
        _PERIOD_PATTERNS[1],
        lambda m: f"FY{m.group(1)}{_BASIS.get(m.group(2) or '', '')}",
    )

    ordered: list[str] = []
    for _, period in sorted(found):
        if period not in ordered:
            ordered.append(period)
    return ordered, working


def split_period(text: str) -> tuple[str, str | None]:
    """A label split into what it measures and when.

    "Revenue CAGR 2023A-2027E" is one label carrying both, and a two-column
    "Metric | Value" table states it that way as a matter of course. Reading
    only the other axis for the metric filed every such row under "value",
    which is generic, so it was never compared with anything -- and CO-006 had
    nothing to recompute.

    The period is removed from the residue rather than merely identified, so
    the metric that comes back is the metric: "revenue cagr", not "revenue
    cagr 2023a-2027e", which would match no other statement of it.
    """
    periods = _all_periods(text)
    if not periods:
        return normalise_label(text), None
    residue = normalise_label(_without_periods(text))
    return residue, parse_period(text)


def parse_period(text: str) -> str | None:
    """The period a label names, folded to one spelling, or None.

    Returns ``FY2024``, ``FY2024E``, ``Q1-2024``, ``H1-2024``, one of the
    relative windows, or a range like ``FY2023A..FY2027E``. ``None`` means the
    label names no period, which is not the same as naming an unknown one: a
    figure with no period is compared under weaker rules, never under none.

    **A label naming more than one period names a range, not the first of them.**
    The reference deck's cumulative row is labelled "Total 2023A-2027E"; read as
    2023A, its 9,828 of five-year revenue was matched against the 1,284 the chart
    plots for 2023 alone, and the clean deck -- the one deck in the suite that
    must be silent -- reported a contradiction with itself. A range compares
    against the same range and against nothing else, which is the only claim the
    label supports.
    """
    periods = _all_periods(text)
    if not periods:
        return None
    if len(periods) == 1:
        return periods[0]
    if periods[0] in _RELATIVE_CANONICAL:
        # A relative window names one period and the year anchors it: "LTM
        # September 2026" is the twelve months to that date, not a range from
        # LTM to 2026. The anchor is kept rather than dropped, so a bare "LTM"
        # elsewhere in the deck does not match it. That costs a finding where a
        # deck writes the same window both ways, and it refuses to invent one
        # where a deck carries an LTM to September 2025 and another to
        # September 2026 -- which is the trade this module makes everywhere.
        return "-".join(periods)
    return f"{periods[0]}..{periods[-1]}"


def periods_agree(one: str | None, other: str | None) -> bool:
    """Whether two periods may describe the same figure.

    ``None`` on either side is *unknown*, not *any*: it agrees, because refusing
    it would silence every figure stated in a sentence that does not repeat the
    year. What never agrees is two periods that are both known and different —
    including the same year on a different basis, since "2025A" and "2025E" are
    different claims and a tool that conflated them would report every forecast.
    """
    if one is None or other is None:
        return True
    return one == other


# --------------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------------

#: Suffixes that scale a figure rather than describing what kind of thing it is.
_SCALE_FACTORS: Final[dict[str, float]] = {
    "k": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "bn": 1e9,
}

#: Suffixes that say what kind of quantity a figure is. Two figures of different
#: kinds are never the same figure, whatever they are labelled.
_QUANTITIES: Final[frozenset[str]] = frozenset({"%", "x", "bp", "bps", "p.a."})

#: A scale stated in words for a whole table or chart: "in US$ millions",
#: "EUR m", "figures in thousands".
_STATED_SCALE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:in\s+)?(?P<scale>millions?|billions?|thousands?|m|mm|bn|k)\b",
    re.IGNORECASE,
)

_WORDED_SCALE: Final[dict[str, str]] = {
    "million": "m", "millions": "m", "m": "m", "mm": "m",
    "billion": "bn", "billions": "bn", "bn": "bn",
    "thousand": "k", "thousands": "k", "k": "k",
}

_CURRENCY_WORD: Final[re.Pattern[str]] = re.compile(
    r"(US\$|U\.S\.\$|USD|EUR|GBP|CHF|JPY|CNY|AUD|CAD|SEK|NOK|DKK|\$|£|€|¥)",
    re.IGNORECASE,
)

_CURRENCY_FOLD: Final[dict[str, str]] = {
    "US$": "USD", "U.S.$": "USD", "$": "USD", "USD": "USD",
    "£": "GBP", "GBP": "GBP",
    "€": "EUR", "EUR": "EUR",
    "¥": "JPY", "JPY": "JPY", "CNY": "CNY",
}


def _fold_currency(token: str | None) -> str | None:
    if not token:
        return None
    key = token.replace(" ", "").upper()
    return _CURRENCY_FOLD.get(key, key)


@dataclass(frozen=True, slots=True)
class Unit:
    """What a figure is measured in.

    ``quantity`` is what kind of thing it is (a percentage, a multiple, basis
    points, or None for a plain count or amount); ``scale`` is the multiplier its
    digits are written against; ``currency`` is the denomination where there is
    one. They are kept apart because they fail differently: a quantity mismatch
    means these are not the same figure at all, while a scale or currency
    mismatch means they may well be, and stated wrongly — which is a finding
    rather than a reason not to look.
    """

    quantity: str | None = None
    scale: str | None = None
    currency: str | None = None

    @property
    def factor(self) -> float | None:
        """The multiplier the digits are written against, where it is known."""
        return _SCALE_FACTORS.get(self.scale) if self.scale else None

    def with_inherited(self, other: Unit) -> Unit:
        """This unit, taking from ``other`` only what it does not state itself.

        A cell reading "1,908" under a table captioned "Figures in US$ millions"
        inherits both scale and currency. The cell always wins where it states
        something, because it is closer to the figure.

        **A figure that states its own quantity inherits neither.** "Figures in
        US$ millions" governs the amounts in a table, not the margin column
        beside them: a 15.3% that inherited the caption came out as a canonical
        value of 15,300,000, and any rule comparing canonical values would then
        have read every margin in the deck as a nine-figure sum. A percentage, a
        multiple and a basis point are dimensionless -- that is what makes them
        that kind of quantity -- so there is nothing for a scale to scale.
        """
        if self.quantity is not None:
            return self
        return Unit(
            quantity=other.quantity,
            scale=self.scale or other.scale,
            currency=self.currency or other.currency,
        )


def _unit_of(reading: NumberReading) -> Unit:
    """The unit a figure states about itself, from its own text."""
    suffix = (reading.suffix or "").casefold()
    return Unit(
        quantity=suffix if suffix in _QUANTITIES else None,
        scale=_WORDED_SCALE.get(suffix) if suffix in _SCALE_FACTORS else None,
        currency=_fold_currency(reading.currency),
    )


def stated_unit(text: str) -> Unit:
    """The unit a caption, header or axis title states for everything under it.

    Only a *scale* and a *currency* are taken. A caption saying "margin (%)"
    does not make every figure beneath it a percentage — the column beside it
    may be an amount — and a quantity guessed from a caption is precisely the
    kind of inference that turns a tie-out into a nuisance.
    """
    if not text:
        return Unit()
    currency = _CURRENCY_WORD.search(text)
    scale_match = _STATED_SCALE.search(text)
    scale: str | None = None
    if scale_match is not None:
        scale = _WORDED_SCALE.get(scale_match.group("scale").casefold())
    return Unit(scale=scale, currency=_fold_currency(currency.group(1) if currency else None))


# --------------------------------------------------------------------------------------
# The figure
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Figure:
    """One number in the deck, with enough identity to compare it to another."""

    reading: NumberReading
    slide_index: int
    #: The shape this figure sits in. Carried whole rather than picked apart
    #: because a finding needs it whole: :meth:`Rule.finding` takes the ref, and
    #: ``uid`` and ``shape_id`` do different jobs. ``uid`` is identity and the
    #: de-duplication key, since ``cNvPr@id`` is not unique in practice;
    #: ``shape_id`` is what ``retext_fix`` writes by, alongside the address
    #: below.
    ref: ShapeRef
    #: Where the shape is, so a finding can point at it without a second walk.
    bbox_pt: tuple[float, float, float, float]
    source: Source
    #: ``(row, column)`` in a table, ``(paragraph, run)`` in text,
    #: ``(series, point)`` in a chart.
    address: tuple[int, int]
    #: The folded label this figure is a measure of.
    metric: str
    #: How that label was arrived at, for the evidence line of any finding.
    metric_from: str
    period: str | None
    unit: Unit
    #: The scope the metric sits in, where the deck gives one: the entity a
    #: trading-comparables row names, for instance. Two figures with different
    #: scopes are never compared, which is what keeps a group table and a
    #: segment table apart.
    scope: str = ""
    #: The text exactly as the deck writes it, for a replacement that has to be
    #: written in the original's own format.
    raw: str = ""
    #: For a table figure, ``(paragraph, run)`` *inside the cell* — the address
    #: a write needs on top of ``(row, column)``. A cell is a text frame like
    #: any other, and a figure carrying a footnote marker ("58.1 (a)") has its
    #: digits in one run and its marker in the next; rewriting the whole cell
    #: would take the marker with it.
    cell_run: tuple[int, int] = (0, 0)

    @property
    def place(self) -> tuple[int, int]:
        """What identifies the shape this figure sits in, deck-wide.

        ``uid`` is unique *within a slide*, in document order -- so the first
        table on slide 2 and the first table on slide 3 both carry uid 0, and
        anything comparing uids alone concludes they are the same shape. That is
        exactly what happened: every cross-slide contradiction in the suite went
        silent at once, because each pair was being discarded as a figure
        compared with itself. The slide index is not decoration here.
        """
        return (self.slide_index, self.ref.uid)

    @property
    def uid(self) -> int:
        return self.ref.uid

    @property
    def shape_id(self) -> int:
        return self.ref.shape_id

    @property
    def shape_name(self) -> str:
        return self.ref.name

    @property
    def row(self) -> int | None:
        return self.address[0] if self.source == "table" else None

    @property
    def column(self) -> int | None:
        return self.address[1] if self.source == "table" else None

    @property
    def paragraph(self) -> int | None:
        return self.address[0] if self.source == "text" else None

    @property
    def run(self) -> int | None:
        return self.address[1] if self.source == "text" else None

    @property
    def series(self) -> int | None:
        return self.address[0] if self.source == "chart" else None

    @property
    def point(self) -> int | None:
        return self.address[1] if self.source == "chart" else None

    @property
    def key(self) -> tuple[str, str, str | None]:
        """What makes two figures candidates for comparison at all."""
        return (self.metric, self.scope, self.unit.quantity)

    @property
    def canonical(self) -> float | None:
        """The value in absolute terms, where the scale is known.

        ``None`` where it is not, so a caller can tell a figure it can convert
        from one it must compare as written.
        """
        factor = self.unit.factor
        return self.reading.value * factor if factor is not None else None

    @property
    def comparable(self) -> bool:
        return is_specific(self.metric)

    @property
    def where(self) -> str:
        """How a finding names this figure's home."""
        if self.source == "chart":
            return f"the chart on slide {self.slide_index}"
        if self.source == "text":
            return f"the text on slide {self.slide_index}"
        return f"{self.shape_name} on slide {self.slide_index}"


def comparable(one: Figure, other: Figure) -> str | None:
    """The confidence at which these two may be compared, or None.

    The whole false-positive discipline is in this one function, so that every
    rule inherits it rather than re-deriving it and getting it slightly wrong.
    """
    if not (one.comparable and other.comparable):
        return None
    if one.key != other.key:
        return None
    if one.place == other.place and one.address == other.address:
        return None
    if not periods_agree(one.period, other.period):
        return None

    sources = {one.source, other.source}
    if "text" in sources:
        # A prose anchor is the weakest evidence there is: the metric came from
        # words near the number rather than from a label above it. At least one
        # side has to name a period, or "Revenue grew to $412m" ties itself to
        # whichever year happens to disagree.
        if one.period is None and other.period is None:
            return None
        return "medium"
    if "chart" in sources:
        # A chart point is identified by its series and its category, and the
        # category is where the period comes from. Without one there is nothing
        # holding the point to a particular column of the table.
        if one.period is None or other.period is None:
            return None
        return "high"
    return "high"


# --------------------------------------------------------------------------------------
# Building the index
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FigureIndex:
    """Every figure in a deck, and the vocabulary it is stated in."""

    figures: tuple[Figure, ...]
    #: Metric labels the deck's tables and charts use, longest first. What prose
    #: is grounded in; see the module docstring for why it is not free text.
    vocabulary: tuple[str, ...]

    def __iter__(self) -> Iterator[Figure]:
        return iter(self.figures)

    def __len__(self) -> int:
        return len(self.figures)

    def of_source(self, source: Source) -> tuple[Figure, ...]:
        return tuple(figure for figure in self.figures if figure.source == source)

    def lookup(
        self,
        aliases: Sequence[str],
        *,
        scope: str,
        period: str | None,
        near: Figure | None = None,
    ) -> Figure | str:
        """The one figure naming one of ``aliases`` in this scope and period.

        Returns the figure, or a *reason* string saying why there is not exactly
        one. The reason is returned rather than swallowed because for a
        pre-send check "I could not verify this" and "this is fine" must never
        look the same: a derived rule that finds no denominator has to say so,
        not fall silent and be counted among the rules that ran.

        ``near`` narrows before it widens. A margin stated in a table is derived
        from that table's own revenue and EBITDA; only if the table does not
        carry them is the slide searched, and only then the deck. Searching the
        deck first means a segment table's margin is checked against the group's
        revenue, which is arithmetic applied to two unrelated numbers.

        The period must match **exactly**, not merely fail to conflict. A figure
        with no period may stand in for one that has none, and for nothing else:
        recomputing a margin needs the inputs for that year, and "close enough"
        is how a rule ends up dividing FY24 EBITDA by FY25 revenue and reporting
        the answer as a defect.
        """
        wanted = {alias.casefold() for alias in aliases}
        candidates = [
            figure
            for figure in self.figures
            if figure.metric in wanted
            and figure.scope == scope
            and figure.period == period
            and figure.unit.quantity is None
        ]
        if not candidates:
            return f"no figure labelled {' or '.join(sorted(wanted))} for this period"

        for tier in _tiers(candidates, near):
            if not tier:
                continue
            values = {round(figure.reading.value, 6) for figure in tier}
            if len(values) > 1:
                return (
                    f"{' or '.join(sorted(wanted))} is stated more than one way for "
                    f"this period, so there is no single figure to compute from"
                )
            return tier[0]
        return f"no figure labelled {' or '.join(sorted(wanted))} for this period"

    def grouped(self) -> dict[tuple[str, str, str | None], list[Figure]]:
        """Figures gathered by what they claim to measure.

        Only the comparable ones. A group of one is kept: a rule that recomputes
        a margin from its inputs wants every figure it can find, not only the
        ones stated twice.
        """
        out: dict[tuple[str, str, str | None], list[Figure]] = {}
        for figure in self.figures:
            if figure.comparable:
                out.setdefault(figure.key, []).append(figure)
        for group in out.values():
            group.sort(key=lambda f: (f.slide_index, f.uid, f.address))
        return out


def restate(reading: NumberReading, value: float) -> str:
    """``value``, written the way the deck writes ``reading``.

    This is the detail most likely to be skipped and most likely to be noticed.
    A fix that corrects 23.1 to 23.4 and drops a currency prefix, a thousands
    separator or a decimal place has introduced a formatting defect while
    fixing an arithmetic one -- and TieOut's own typography rules will then
    report it, on a slide TieOut itself just edited.

    So every property the reading carries is carried through: the number of
    decimals, the thousands separator and which one it is, whether a negative
    is written with a minus or in parentheses, the currency prefix, and the
    suffix. What is *not* carried through is the sign convention where the sign
    changes: a figure written "(42)" that corrects to a positive is written
    "42", because writing "(42)" for a positive number is worse than either.

    The replacement is a string rather than a number for the same reason
    ``Finding.measured`` is: the unit and the precision are part of what the
    cell says.
    """
    magnitude = abs(value)
    body = f"{magnitude:,.{reading.decimals}f}"
    if reading.thousands_separator is None:
        body = body.replace(",", "")
    elif reading.thousands_separator != ",":
        body = body.replace(",", reading.thousands_separator)

    if reading.currency:
        body = f"{reading.currency}{body}"
    if reading.suffix:
        body = f"{body}{reading.suffix}"

    if value >= 0:
        return body
    if reading.negative_style == "parentheses":
        return f"({body})"
    return f"-{body}"


def _tiers(
    candidates: Sequence[Figure], near: Figure | None
) -> tuple[list[Figure], list[Figure], list[Figure]]:
    """``candidates`` split into same shape, same slide, and anywhere."""
    if near is None:
        return ([], [], list(candidates))
    return (
        [f for f in candidates if f.place == near.place],
        [f for f in candidates if f.slide_index == near.slide_index],
        list(candidates),
    )


def build_index(deck: DeckModel) -> FigureIndex:
    """Walk the deck once and read every figure in it.

    Tables and charts first, because they are where reliable labels live and
    because prose is grounded in the vocabulary they establish.
    """
    table_figures: list[Figure] = []
    chart_figures: list[Figure] = []
    for slide in deck.slides:
        for shape in slide.tables:
            table_figures.extend(_table_figures(slide, shape))
        for shape in slide.charts:
            chart_figures.extend(_chart_figures(slide, shape))

    vocabulary = _vocabulary(table_figures, chart_figures)

    text_figures: list[Figure] = []
    for slide in deck.slides:
        for shape in slide.leaf_shapes():
            if shape.table is not None or shape.chart is not None:
                continue
            text_figures.extend(_text_figures(slide, shape, vocabulary))

    figures = tuple(
        sorted(
            [*table_figures, *chart_figures, *text_figures],
            key=lambda f: (f.slide_index, f.uid, f.source, f.address),
        )
    )
    return FigureIndex(figures=figures, vocabulary=vocabulary)


def _vocabulary(*groups: Sequence[Figure]) -> tuple[str, ...]:
    """Metric labels the deck establishes, longest first.

    Longest first because matching is greedy: "free cash flow" must win over
    "cash flow" wherever both would match, or a sentence about the first gets
    filed under the second.
    """
    seen = {
        figure.metric
        for group in groups
        for figure in group
        if is_specific(figure.metric)
    }
    return tuple(sorted(seen, key=lambda label: (-len(label), label)))


# -- tables -----------------------------------------------------------------------------


def _table_figures(slide: SlideModel, shape: ShapeModel) -> list[Figure]:
    """Every numeric cell in one table, labelled by its row and column.

    Row labels come from the first column and column labels from the header row,
    which is how every banking table is built. A table with neither is skipped
    rather than guessed at.

    Which axis carries the *metric* and which the *period* is read from the
    labels rather than assumed. A fiscal-year table puts periods down the rows
    and metrics across the columns; a trading-comparables table puts entities
    down the rows and metrics across. Assuming one shape reports the other.
    """
    table = shape.table
    if table is None or table.row_count < 2 or table.column_count < 2:
        return []

    headers = {
        column: normalise_label(cell.text)
        for column in range(table.column_count)
        if (cell := table.cell(0, column)) is not None
    }
    header_units = {
        column: stated_unit(cell.text)
        for column in range(table.column_count)
        if (cell := table.cell(0, column)) is not None
    }
    caption = _inherited_unit(slide, shape)

    out: list[Figure] = []
    for row in range(1, table.row_count):
        label_cell = table.cell(row, 0)
        if label_cell is None:
            continue
        row_label = normalise_label(label_cell.text)
        if not row_label:
            continue
        row_metric, row_period = split_period(label_cell.text)

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

            column_label = headers.get(column, "")
            column_metric, column_period = split_period(column_label)

            # Whichever axis names a period is the period; the other names the
            # metric. Where neither does, the row is the scope and the column is
            # the metric, which is how a comparables table reads.
            #
            # The metric is taken from the *other* axis only while that axis has
            # something specific to say. A "Metric | Value" summary table puts
            # the whole of "Revenue CAGR 2023A-2027E" in the row label and the
            # word "Value" in the header, and reading the header filed every such
            # row under a generic label where nothing could ever match it. Where
            # the other axis is generic, the naming axis keeps what is left of
            # itself once the period is taken out.
            if row_period is not None and column_period is None:
                metric = column_metric if is_specific(column_metric) else row_metric
                scope, period = "", row_period
            elif column_period is not None and row_period is None:
                metric = row_metric if is_specific(row_metric) else column_metric
                scope, period = "", column_period
            elif row_period is not None and column_period is not None:
                # Both. The row wins as the period and the column keeps its own,
                # which is a sub-period of it; comparing across them needs both,
                # so the scope carries the column's.
                metric, scope, period = column_metric, column_period, row_period
            else:
                metric, scope, period = column_label, row_label, None

            unit = _unit_of(reading).with_inherited(
                header_units.get(column, Unit()).with_inherited(caption)
            )
            inside = _cell_run_holding(cell.paragraphs, reading.raw)
            out.append(
                Figure(
                    reading=reading,
                    slide_index=slide.index,
                    ref=shape.ref,
                    bbox_pt=shape.bbox_pt,
                    source="table",
                    address=(row, column),
                    metric=metric,
                    metric_from=(
                        f"row {row_label!r} and column {column_label!r}"
                        if column_label
                        else f"row {row_label!r}"
                    ),
                    period=period,
                    unit=unit,
                    scope=scope,
                    raw=text.strip(),
                    cell_run=inside,
                )
            )
    return out


def _cell_run_holding(paragraphs: Sequence[object], value: str) -> tuple[int, int]:
    """Which ``(paragraph, run)`` of a cell holds the figure's digits.

    Falls back to ``(0, 0)``, which is right for the overwhelming majority of
    cells -- one paragraph, one run, the number and nothing else. The search
    matters for the minority: a cell reading "58.1 (a)" often carries the
    marker in a superscripted run of its own, and a correction that rewrote run
    zero with the new figure alone would silently drop the footnote.
    """
    digits = value.strip()
    for p_index, paragraph in enumerate(paragraphs):
        for r_index, run in enumerate(getattr(paragraph, "runs", ())):
            if digits and digits in getattr(run, "text", ""):
                return (p_index, r_index)
    return (0, 0)


def _inherited_unit(slide: SlideModel, shape: ShapeModel) -> Unit:
    """A scale stated once for a whole table or chart, and inherited by it.

    "Figures in US$ millions" is written outside the table, so a cell reading
    1,908 means 1.908bn, and the same cell under "US$ thousands" does not.
    Getting this wrong is not a near miss: it puts a figure three orders of
    magnitude out with full confidence.

    Three places are looked at, nearest first:

    1. a text shape directly above the table or chart and horizontally
       overlapping it -- the caption, where a deck that captions puts it;
    2. any other text shape on the slide that states a unit. **A footnote
       counts.** This was written looking above only, on the reasonable-sounding
       argument that a caption sits over what it captions. Every slide in the
       reference deck states its scale in the source line at the foot -- "Source:
       Company management. Figures in US$ millions." -- which is what banking
       decks actually do, and the rule found nothing on any of them;
    3. the slide title.

    The slide is the boundary. A slide carrying two tables in different scales
    and one footnote will hand both the same unit, which is a real limit and is
    also a deck that should say so.
    """
    above: tuple[float, Unit] | None = None
    elsewhere: Unit | None = None
    for other in slide.leaf_shapes():
        if other.ref.uid == shape.ref.uid or not other.has_text:
            continue
        unit = stated_unit(other.text)
        if unit == Unit():
            continue
        overlaps = not (
            other.right_pt < shape.left_pt or other.left_pt > shape.right_pt
        )
        if overlaps and other.bottom_pt <= shape.top_pt + 2.0:
            gap = shape.top_pt - other.bottom_pt
            if above is None or gap < above[0]:
                above = (gap, unit)
        elif elsewhere is None:
            elsewhere = unit
    if above is not None:
        return above[1]
    if elsewhere is not None:
        return elsewhere
    return stated_unit(slide.title_text)


# -- charts -----------------------------------------------------------------------------


def _chart_figures(slide: SlideModel, shape: ShapeModel) -> list[Figure]:
    """Every plotted point, named by its series and its category.

    The series names the metric and the category names the period, which is how
    a reader reads the chart. A series with no name, or a chart with no
    categories, yields nothing rather than a point nobody can attribute.
    """
    chart = shape.chart
    if chart is None:
        return []

    caption = stated_unit(
        " ".join(part for part in (chart.title_text or "", *chart.axis_titles) if part)
    ).with_inherited(_inherited_unit(slide, shape))

    out: list[Figure] = []
    for series_index, series in enumerate(chart.series):
        metric = normalise_label(series.name or "")
        if not is_specific(metric):
            continue
        for point_index, value in enumerate(series.values):
            if value is None:
                continue
            if point_index >= len(chart.categories):
                continue
            category = chart.categories[point_index]
            period = parse_period(category)
            # A category that is not a period is a scope: the "Low" bar of a
            # football field belongs to its methodology, and a Low under
            # "Trading comparables" is a different fact from a Low under "DCF".
            scope = "" if period is not None else normalise_label(category)
            reading = NumberReading(
                raw=_format_plain(value),
                value=value,
                # The cache holds a float, not a formatted string, so the
                # displayed precision is the label format's business rather than
                # this module's. Claiming a precision here would let a fix write
                # a chart's value into a table cell at the wrong one.
                decimals=0,
                thousands_separator=None,
                negative_style="minus" if value < 0 else None,
                currency=None,
                suffix=None,
            )
            out.append(
                Figure(
                    reading=reading,
                    slide_index=slide.index,
                    ref=shape.ref,
                    bbox_pt=shape.bbox_pt,
                    source="chart",
                    address=(series_index, point_index),
                    metric=metric,
                    metric_from=f"series {series.name!r}, category {category!r}",
                    period=period,
                    unit=caption,
                    scope=scope,
                    raw=_format_plain(value),
                )
            )
    return out


def _format_plain(value: float) -> str:
    return f"{value:,.10g}"


# -- prose ------------------------------------------------------------------------------

#: A figure inside a sentence, with enough around it to read. Deliberately
#: narrower than :func:`tieout.text.parse_number`, which is anchored at both
#: ends: here the number sits inside other words.
_IN_PROSE: Final[re.Pattern[str]] = re.compile(
    r"""
    (?<![\w.])
    (?P<open>\()?
    \s*
    (?P<currency>US\$|U\.S\.\$|USD|EUR|GBP|JPY|CHF|\$|£|€|¥)?
    \s*
    (?P<minus>-|−)?
    (?P<int>\d{1,3}(?:[,  ]\d{3})+|\d+)
    (?:\.(?P<frac>\d+))?
    \s?
    (?P<suffix>%|x\b|bps?\b|p\.a\.|m\b|bn\b|k\b|mm\b)?
    (?P<close>\))?
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: Splits a paragraph into sentences for the purpose of "is the metric named
#: near this number". Crude on purpose: a bullet is one sentence whether or not
#: it is punctuated, and a semicolon separates two claims.
_SENTENCE: Final[re.Pattern[str]] = re.compile(r"[.;!?]\s+|\n")

#: The window in which a bare four-digit integer is a year rather than a figure.
#: See :func:`_is_a_year`.
_YEAR_RANGE: Final[tuple[int, int]] = (1900, 2099)


def _is_a_year(reading: NumberReading) -> bool:
    """Whether a figure found in prose is a date rather than a measurement.

    "Revenue has compounded at twenty one per cent since 2022" contains one
    number, and it is not a revenue figure. Indexing it as one gave the clean
    reference deck a Revenue of 2,022 to contradict its own table with, on the
    very first run of this module -- which is the kind of finding that gets a
    tie-out switched off in an afternoon.

    Every condition has to hold at once: four digits, no decimal, no thousands
    separator, no currency, no suffix, not negative, and inside the window a
    year plausibly falls in. A revenue of exactly 2,022 written as "2,022"
    carries its separator and is read as a figure; written as "2022" in prose it
    is indistinguishable from the year and is not indexed. That loss is the
    price of not inventing the other kind of finding.
    """
    low, high = _YEAR_RANGE
    return (
        reading.decimals == 0
        and reading.thousands_separator is None
        and reading.currency is None
        and reading.suffix is None
        and reading.negative_style is None
        and float(reading.value).is_integer()
        and low <= int(reading.value) <= high
    )


def _text_figures(
    slide: SlideModel, shape: ShapeModel, vocabulary: Sequence[str]
) -> list[Figure]:
    """Figures stated in prose, grounded in the deck's own metric vocabulary.

    A number is indexed only where the sentence around it names a metric the
    deck's tables or charts already use. See the module docstring: the
    alternative invents vocabulary, and a rule keyed on invented vocabulary
    reports the deck for things nobody wrote.
    """
    if not shape.has_text or not vocabulary:
        return []

    out: list[Figure] = []
    for paragraph_index, paragraph in enumerate(shape.text_frame_paragraphs):
        text = paragraph.text
        if not text.strip():
            continue
        for sentence, start in _sentences(text):
            metric = _metric_in(sentence, vocabulary)
            if metric is None:
                continue
            period = parse_period(sentence)
            for match in _IN_PROSE.finditer(sentence):
                reading = _reading_from(match)
                if reading is None or _is_a_year(reading):
                    continue
                run_index = _run_holding(paragraph.runs, start + match.start("int"))
                if run_index is None:
                    continue
                out.append(
                    Figure(
                        reading=reading,
                        slide_index=slide.index,
                        ref=shape.ref,
                        bbox_pt=shape.bbox_pt,
                        source="text",
                        address=(paragraph_index, run_index),
                        metric=metric,
                        metric_from=f"the sentence {_ellipsis(sentence)!r}",
                        period=period,
                        unit=_unit_of(reading),
                        raw=match.group(0).strip(),
                    )
                )
    return out


def _sentences(text: str) -> list[tuple[str, int]]:
    """Each sentence with its offset in the paragraph.

    The offset is what lets a figure address back to the run that holds its
    digits, which is what a fix needs.
    """
    out: list[tuple[str, int]] = []
    position = 0
    for piece in _SENTENCE.split(text):
        found = text.find(piece, position)
        if found < 0:  # pragma: no cover - split always yields substrings
            found = position
        out.append((piece, found))
        position = found + len(piece)
    return out


def _metric_in(sentence: str, vocabulary: Sequence[str]) -> str | None:
    """The deck's own metric this sentence names, longest match first."""
    folded = " ".join(sentence.split()).casefold()
    for label in vocabulary:
        if re.search(rf"(?<![\w]){re.escape(label)}(?![\w])", folded):
            return label
    return None


def _reading_from(match: re.Match[str]) -> NumberReading | None:
    """A :class:`NumberReading` from a figure found inside a sentence.

    Shares :func:`tieout.text.parse_number`'s output type on purpose: a fix that
    corrects a figure has to write the replacement in the original's own format,
    and the decimals, separator, negative style, currency and suffix are exactly
    what that needs.
    """
    groups = match.groupdict()
    integer = groups["int"]
    separator: str | None = None
    for candidate in (",", " ", " "):
        if candidate in integer:
            separator = candidate
            break
    digits = re.sub(r"[,  ]", "", integer)
    fraction = groups["frac"] or ""
    try:
        magnitude = float(f"{digits}.{fraction}") if fraction else float(digits)
    except ValueError:  # pragma: no cover - the pattern only matches digits
        return None

    negative_style: str | None = None
    if groups["open"] and groups["close"]:
        negative_style = "parentheses"
    elif groups["minus"]:
        negative_style = "minus"
    elif groups["open"] or groups["close"]:
        # An unbalanced bracket in prose is ordinary punctuation -- "(see 12)" --
        # rather than a negative, so it is read as a positive figure. That is the
        # opposite of the table reading, where an unbalanced bracket is a defect
        # in the cell, and the difference is deliberate: prose has brackets in it.
        negative_style = None

    return NumberReading(
        raw=match.group(0).strip(),
        value=-magnitude if negative_style else magnitude,
        decimals=len(fraction),
        thousands_separator=separator,
        negative_style=negative_style,
        currency=groups["currency"],
        suffix=(groups["suffix"] or "").strip() or None,
    )


def _run_holding(runs: Sequence[object], offset: int) -> int | None:
    """Which run of the paragraph contains the character at ``offset``.

    A figure split across runs by a stray formatting change -- "$4" in one run
    and "12m" in the next -- reads as one number, and addresses back to the run
    that holds its first digit. That is the run a fix has to rewrite; the rest
    of the figure lives in runs a fix would have to clear, which is why
    :mod:`tieout_ui.edit` refuses rather than guessing.
    """
    position = 0
    for index, run in enumerate(runs):
        length = len(getattr(run, "text", ""))
        if position <= offset < position + length:
            return index
        position += length
    return None


def _ellipsis(text: str, limit: int = 60) -> str:
    folded = " ".join(text.split())
    return folded if len(folded) <= limit else folded[: limit - 1] + "…"
