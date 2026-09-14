"""Typography rules: quotes, whitespace, bullets, headings, terminology, numbers,
currency, dates and spelling.

Every measurement here comes from :mod:`tieout.text`, which the typography
deriver also calls. That is the whole point of the shared module: if a rule
parsed a number or a date differently from the deriver, TieOut would report
defects on the very deck it learned the convention from.

Two conventions run through the category:

* A ``None`` field in :class:`~tieout.profile.schema.TypographyProfile` means the
  convention was never learned, so it is never checked. The engine enforces this
  through :attr:`~tieout.rules.base.Rule.requires`; no rule here carries a
  fallback default.
* Deck chrome is excluded from the prose rules. The confidentiality line and the
  page number are boilerplate the author does not retype, and a deviation in
  them is the footer rules' business rather than typography's.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from typing import ClassVar, Final

from tieout.model.archetype import CONTENT_ARCHETYPES
from tieout.model.deck import DeckModel, ShapeModel, SlideModel, TableModel
from tieout.model.furniture import Furniture
from tieout.profile.schema import Profile, Severity
from tieout.rules.base import Finding, Rule, cluster_findings, register
from tieout.text import (
    NumberReading,
    WhitespaceDefect,
    bullet_terminal,
    canon_key,
    capitalisation_style,
    currency_tokens,
    find_dates,
    is_numeric_placeholder,
    normalise_currency,
    parse_number,
    quote_census,
    spell_tokens,
    spell_variants,
    whitespace_defects,
)

# --------------------------------------------------------------------------------------
# Shared text traversal
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Passage:
    """One addressable run of text, with the shape a finding should point at.

    Paragraph, table cell and chart label are kept separate rather than
    concatenated: a whitespace or quote defect measured across a joined string
    would report a position no reader could find.
    """

    shape: ShapeModel
    text: str
    source: str


def _passages(
    slide: SlideModel, *, furniture: Furniture | None = None
) -> Iterator[_Passage]:
    """Every piece of text on a slide: text frames, table cells, chart labels.

    Passing ``furniture`` excludes deck chrome. Charts are included through
    ``chart.text_strings`` because series names and category labels are prose the
    author typed, and they obey the same conventions as the body.
    """
    for shape in slide.leaf_shapes():
        if furniture is not None and furniture.is_furniture(
            slide.index, shape.ref.shape_id
        ):
            continue
        for paragraph in shape.text_frame_paragraphs:
            if paragraph.text.strip():
                yield _Passage(shape, paragraph.text, "paragraph")
        if shape.table is not None:
            for cell in shape.table.cells:
                if cell.is_merge_continuation or not cell.text.strip():
                    continue
                yield _Passage(shape, cell.text, "table cell")
        if shape.chart is not None:
            for label in shape.chart.text_strings:
                if label.strip():
                    yield _Passage(shape, label, "chart label")


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else f"{singular}s"


# --------------------------------------------------------------------------------------
# TY-001 quotes
# --------------------------------------------------------------------------------------


@register
class QuoteStyle(Rule):
    """Quote and apostrophe glyphs that contradict the learned convention.

    Measures: the count of curly and straight quote glyphs on each slide, from
    :func:`tieout.text.quote_census`, which already discards measurement marks
    such as ``5' 6"``. Fires once per slide, and only when a glyph of the wrong
    style is actually present -- a slide with no quotes at all is not a
    deviation.

    Known false positive: a deliberate straight glyph inside quoted code, a file
    path or a ticker written with a prime is reported as a deviation, because the
    model carries no notion of a literal span.
    """

    id: ClassVar[str] = "TY-001"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "minor"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = (
        "Quote or apostrophe style contradicts the learned convention"
    )
    requires: ClassVar[tuple[str, ...]] = ("typography.quotes",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        convention = profile.typography.quotes
        if convention is None:  # pragma: no cover - the engine skips on requires
            return self.skip("the profile does not define typography.quotes")

        wrong = "straight" if convention == "curly" else "curly"
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []

        for slide in deck.slides:
            offenders: list[_Passage] = []
            count = 0
            for passage in _passages(slide, furniture=furniture):
                census = quote_census(passage.text)
                seen = census.straight if wrong == "straight" else census.curly
                if seen:
                    offenders.append(passage)
                    count += seen
            if not offenders:
                continue

            first = offenders[0]
            findings.append(
                self.finding(
                    where=first.shape.ref,
                    message=(
                        f"{count} {wrong} quote {_plural(count, 'glyph')} against the "
                        f"deck's {convention} convention"
                    ),
                    profile=profile,
                    provenance_path="typography.quotes",
                    measured=(
                        f"{count} {wrong} in {len(offenders)} "
                        f"{_plural(len(offenders), 'passage')}"
                    ),
                    expected=f"{convention} quotes and apostrophes",
                    bbox_pt=first.shape.bbox_pt,
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# TY-002 whitespace
# --------------------------------------------------------------------------------------

#: A short roman numeral or digit label followed by a stop or a bracket. Several
#: spaces after one of these are column alignment in a table of contents rather
#: than a typing slip, and reporting them would put TY-002 on the reference
#: deck's own agenda slide.
_ENUMERATOR: Final[re.Pattern[str]] = re.compile(r"^\s*[0-9IVXLCDMivxlcdm]{1,5}[.):]$")


def _is_enumerator_alignment(text: str, defect: WhitespaceDefect) -> bool:
    """Whether a run of spaces is deliberate list-label alignment."""
    if defect.kind != "double space":
        return False
    return bool(_ENUMERATOR.match(text[: defect.position]))


@register
class Whitespace(Rule):
    """Spacing defects: double spaces, trailing whitespace, a space before
    ``,.;:!?`` or a bracket, and non-breaking spaces mixed with ordinary ones.

    Measures: :func:`tieout.text.whitespace_defects` over every paragraph, table
    cell and chart label that is not deck chrome, clustered into one finding per
    slide naming the kinds found and the total count. These are absolute
    typesetting errors rather than a learned convention, so the rule requires
    nothing from the profile and is never skipped for want of evidence.

    Known false positive: multiple spaces used to align text inside a single
    paragraph. The common case -- alignment after a list enumerator such as
    ``II.`` -- is excluded, but a hand-aligned two-column layout built out of
    runs of spaces is reported.
    """

    id: ClassVar[str] = "TY-002"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "minor"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = (
        "Double space, trailing whitespace, space before punctuation or mixed "
        "non-breaking spaces"
    )
    #: Nothing to learn: these are wrong in every house style.
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []

        for slide in deck.slides:
            kinds: dict[str, int] = {}
            first_shape: ShapeModel | None = None
            first_excerpt: str | None = None
            for passage in _passages(slide, furniture=furniture):
                for defect in whitespace_defects(passage.text):
                    if _is_enumerator_alignment(passage.text, defect):
                        continue
                    kinds[defect.kind] = kinds.get(defect.kind, 0) + 1
                    if first_shape is None:
                        first_shape = passage.shape
                        first_excerpt = defect.excerpt
            if first_shape is None or first_excerpt is None:
                continue

            total = sum(kinds.values())
            findings.append(
                self.finding(
                    where=first_shape.ref,
                    message=(
                        f"{total} spacing {_plural(total, 'defect')} on this slide: "
                        + ", ".join(sorted(kinds))
                    ),
                    profile=profile,
                    provenance_path="typography",
                    measured=first_excerpt,
                    expected="single spaces, and no space before punctuation",
                    bbox_pt=first_shape.bbox_pt,
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# TY-003 bullet terminal punctuation
# --------------------------------------------------------------------------------------


@register
class BulletTerminal(Rule):
    """Bullet terminal punctuation inconsistent with the learned convention.

    Measures: :func:`tieout.text.bullet_terminal` for every bulleted paragraph,
    evaluated one list at a time, where a list is the bulleted paragraphs of a
    single shape. A deviating bullet is reported only when the rest of its list
    follows the convention: one full stop in an otherwise unpunctuated list is a
    slip, whereas a list punctuated throughout is a decision the author took at
    the list level and is left alone. Findings are clustered to one per slide.

    Known false positives: an indented paragraph in a body placeholder counts as
    a bullet, because ``TextParagraph.is_bulleted`` treats inherited bullets that
    way; and a list whose final item closes a sentence running across several
    bullets is reported as deviating.
    """

    id: ClassVar[str] = "TY-003"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "minor"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = (
        "Bullet terminal punctuation inconsistent with the learned convention"
    )
    requires: ClassVar[tuple[str, ...]] = ("typography.bullet_terminal_punctuation",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        convention = profile.typography.bullet_terminal_punctuation
        if convention is None:  # pragma: no cover - the engine skips on requires
            return self.skip(
                "the profile does not define typography.bullet_terminal_punctuation"
            )

        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []

        for slide in deck.slides:
            for shape in slide.leaf_shapes():
                if furniture.is_furniture(slide.index, shape.ref.shape_id):
                    continue
                bullets = [
                    paragraph
                    for paragraph in shape.text_frame_paragraphs
                    if paragraph.is_bulleted and not paragraph.is_empty
                ]
                if len(bullets) < 2:
                    if bullets:
                        self.note_unchecked(
                            shape.ref,
                            "a one-item list carries no evidence of a list-level choice",
                        )
                    continue

                terminals = [bullet_terminal(paragraph.text) for paragraph in bullets]
                deviating = [
                    (paragraph, terminal)
                    for paragraph, terminal in zip(bullets, terminals, strict=True)
                    if terminal != convention
                ]
                if not deviating:
                    continue
                if len(deviating) == len(bullets):
                    # The whole list disagrees, which reads as the author's
                    # decision for this list rather than a slip in one item.
                    self.note_unchecked(
                        shape.ref,
                        f"every bullet in this list ends with {terminals[0]}, which is "
                        "a list-level choice rather than a slip",
                    )
                    continue

                paragraph, terminal = deviating[0]
                findings.append(
                    self.finding(
                        where=shape.ref,
                        message=(
                            f"{len(deviating)} of {len(bullets)} bullets end with "
                            f"{terminal} in a list that otherwise ends with {convention}"
                        ),
                        profile=profile,
                        provenance_path="typography.bullet_terminal_punctuation",
                        measured=f"{terminal}: ...{paragraph.text.strip()[-32:]}",
                        expected=convention,
                        bbox_pt=shape.bbox_pt,
                    )
                )
        return cluster_findings(findings)


# --------------------------------------------------------------------------------------
# TY-004 title capitalisation
# --------------------------------------------------------------------------------------


@register
class TitleCapitalisation(Rule):
    """Slide title capitalisation deviating from the learned convention.

    Measures: :func:`tieout.text.capitalisation_style` on each slide's title,
    compared with ``typography.title_case``. Only the content-bearing archetypes
    are checked: a heading on a divider, an agenda or a disclaimer is a fixed
    document label -- "Table of Contents", "Appendix", "Important Notice" -- which
    is conventionally capitalised however the deck's prose headlines are, so
    checking those would report the reference deck's own contents page. A title
    the helper cannot classify is recorded as unchecked rather than passed.

    Known false positive: a prose headline made mostly of proper nouns can read
    as title case. The helper discards words capitalised under both conventions,
    which removes most of these, but a headline whose only decidable word is a
    brand name can still be misclassified.
    """

    id: ClassVar[str] = "TY-004"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "minor"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = (
        "Slide title capitalisation deviates from the learned convention"
    )
    requires: ClassVar[tuple[str, ...]] = ("typography.title_case",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        convention = profile.typography.title_case
        if convention is None:  # pragma: no cover - the engine skips on requires
            return self.skip("the profile does not define typography.title_case")

        findings: list[Finding] = []
        for slide in deck.slides:
            if slide.archetype not in CONTENT_ARCHETYPES:
                self.note_unchecked(
                    slide.index,
                    f"a {slide.archetype} heading is a fixed document label rather "
                    "than a prose headline",
                )
                continue

            title = slide.title_shape
            if title is None:
                self.note_unchecked(slide.index, "no title shape on this slide")
                continue

            text = slide.title_text
            style = capitalisation_style(text)
            if style is None:
                self.note_unchecked(
                    title.ref,
                    "the title is too short to classify as sentence or title case",
                )
                continue
            if style == convention:
                continue

            findings.append(
                self.finding(
                    where=title.ref,
                    message=f"the title reads as {style} case, not {convention} case",
                    profile=profile,
                    provenance_path="typography.title_case",
                    measured=f"{style} case: {text!r}",
                    expected=f"{convention} case",
                    bbox_pt=title.bbox_pt,
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# TY-005 terminology
# --------------------------------------------------------------------------------------

#: One word, keeping the internal punctuation that ``canon_key`` later strips, so
#: that "U.S." and "Board's" stay single tokens.
_WORD_SPAN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’&.\-]*")

#: Sentence punctuation a window may have swallowed from the end of a sentence.
#: Left attached, the canonical form itself would read as a variant.
_TRAILING_PUNCTUATION: Final[str] = ".,;:!?)]'\"’”"


def _windows(text: str, length: int) -> Iterator[str]:
    """Every run of ``length`` consecutive words, exactly as it appears."""
    spans = [(match.start(), match.end()) for match in _WORD_SPAN.finditer(text)]
    for start in range(len(spans) - length + 1):
        yield text[spans[start][0] : spans[start + length - 1][1]]


@register
class CanonicalTerms(Rule):
    """A non-canonical variant of a learned term.

    Measures: every window of words the length of a canonical term from
    ``typography.canon_terms``. A window that shares its
    :func:`tieout.text.canon_key` with the canonical form but differs from it as
    a surface string is a variant, which catches the case slips and misspellings
    the learned variant list never saw. The variants recorded in the profile are
    additionally matched literally, since a short form such as "AP" shares no key
    with what it abbreviates. One finding per slide per variant, reporting the
    canonical form in ``expected``.

    Known false positive: a canonical term whose words are ordinary prose is
    reported at the start of a sentence, where the initial capital is grammar
    rather than a terminology error.
    """

    id: ClassVar[str] = "TY-005"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "major"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "A non-canonical variant of a learned term appears"
    requires: ClassVar[tuple[str, ...]] = ("typography.canon_terms",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        canon_terms = profile.typography.canon_terms
        if not canon_terms:  # pragma: no cover - the engine skips on requires
            return self.skip("the profile does not define typography.canon_terms")

        # Compiled once per deck rather than once per passage: a literal matcher
        # per declared variant, bounded so a longer word is not a hit.
        literals: dict[str, list[tuple[str, re.Pattern[str]]]] = {
            canonical: [
                (
                    variant,
                    re.compile(rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])"),
                )
                for variant in variants
                if variant and variant != canonical
            ]
            for canonical, variants in canon_terms.items()
        }

        findings: list[Finding] = []
        for slide in deck.slides:
            # (canonical, variant) -> the shape it was first seen on, and a count.
            seen: dict[tuple[str, str], tuple[ShapeModel, int]] = {}
            for passage in _passages(slide):
                for canonical in canon_terms:
                    for variant in _variants_in(
                        passage.text, canonical, literals[canonical]
                    ):
                        key = (canonical, variant)
                        shape, count = seen.get(key, (passage.shape, 0))
                        seen[key] = (shape, count + 1)

            for (canonical, variant), (shape, count) in sorted(seen.items()):
                occurrences = "" if count == 1 else f", {count} times on this slide"
                findings.append(
                    self.finding(
                        where=shape.ref,
                        message=(
                            f"{variant!r} is a non-canonical form of "
                            f"{canonical!r}{occurrences}"
                        ),
                        profile=profile,
                        provenance_path="typography.canon_terms",
                        measured=variant,
                        expected=canonical,
                        bbox_pt=shape.bbox_pt,
                    )
                )
        return findings


def _variants_in(
    text: str, canonical: str, literals: list[tuple[str, re.Pattern[str]]]
) -> Iterator[str]:
    """Surface forms in ``text`` that should have been ``canonical``."""
    key = canon_key(canonical)
    length = len(key.split())
    if length:
        for window in _windows(text, length):
            surface = window.rstrip(_TRAILING_PUNCTUATION)
            if surface != canonical and canon_key(surface) == key:
                yield surface
    for variant, pattern in literals:
        if pattern.search(text):
            yield variant


# --------------------------------------------------------------------------------------
# TY-006 number formatting within a table column
# --------------------------------------------------------------------------------------

#: Below this magnitude a figure has no thousands group to separate, so its
#: separator cannot be compared with a larger figure's in the same column.
_SEPARATOR_VISIBLE_FROM: Final[float] = 1000.0


def _column_label(table: TableModel, column: int) -> str:
    """A name for a column, taken from its header cell where there is one."""
    cell = table.cell(0, column)
    if cell is not None:
        text = cell.text.strip()
        if text and len(text) <= 40 and parse_number(text) is None:
            return repr(text)
    return f"column {column + 1}"


def _disagreements(readings: list[NumberReading]) -> list[tuple[str, str]]:
    """The formatting axes a column's figures disagree on, with the evidence.

    Grouped by suffix so a percentage is never compared with a multiple, and
    reported at most once per axis however many groups differ.
    """
    groups: dict[str, list[NumberReading]] = {}
    for reading in readings:
        groups.setdefault((reading.suffix or "").casefold(), []).append(reading)

    out: dict[str, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue

        decimals = sorted({reading.decimals for reading in group})
        if len(decimals) > 1 and "decimal places" not in out:
            shown = " and ".join(str(value) for value in decimals)
            out["decimal places"] = f"{shown} decimal places in one column"

        separable = [
            reading
            for reading in group
            if abs(reading.value) >= _SEPARATOR_VISIBLE_FROM
        ]
        presence = {reading.thousands_separator is not None for reading in separable}
        if len(presence) > 1 and "thousands separator" not in out:
            grouped = sum(
                1 for reading in separable if reading.thousands_separator is not None
            )
            out["thousands separator"] = (
                f"{grouped} of {len(separable)} figures above a thousand carry a "
                "separator"
            )

        negatives = {
            reading.negative_style
            for reading in group
            if reading.negative_style is not None
        }
        if len(negatives) > 1 and "negative style" not in out:
            out["negative style"] = " and ".join(sorted(negatives)) + " negatives"

    return [(kind, out[kind]) for kind in sorted(out)]


@register
class ColumnNumberFormat(Rule):
    """Number formatting that disagrees within one table column.

    Measures: every body cell of every table column, parsed with
    :func:`tieout.text.parse_number`; placeholders such as ``n.a.`` and
    non-numeric cells are excluded. A column is inconsistent when its figures
    disagree on decimal places, on whether a thousands separator is present, or
    -- among the cells that are actually negative -- on how the negative is
    written. Percentages, multiples and basis points are compared only against
    their own kind, because a column holding both a margin and a multiple is
    legitimately formatted two ways. One finding per column.

    Known false positive: a column that deliberately mixes precision, such as a
    ratio quoted to two places beside a whole-number count under one heading, is
    reported as inconsistent.
    """

    id: ClassVar[str] = "TY-006"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "major"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Number formatting is inconsistent within one table column"
    requires: ClassVar[tuple[str, ...]] = ("typography.decimal_places_by_column",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        if profile.typography.decimal_places_by_column is None:  # pragma: no cover
            return self.skip(
                "the profile does not define typography.decimal_places_by_column"
            )

        findings: list[Finding] = []
        for slide in deck.slides:
            for shape in slide.tables:
                table = shape.table
                if table is None:  # pragma: no cover - slide.tables guarantees one
                    continue
                unevaluated = 0
                for column, cells in table.iter_columns(skip_header=True):
                    readings: list[NumberReading] = []
                    for cell in cells:
                        if is_numeric_placeholder(cell.text):
                            continue
                        reading = parse_number(cell.text)
                        if reading is not None:
                            readings.append(reading)
                    if len(readings) < 2:
                        unevaluated += 1
                        continue

                    disagreements = _disagreements(readings)
                    if not disagreements:
                        continue
                    findings.append(
                        self.finding(
                            where=shape.ref,
                            message=(
                                f"{_column_label(table, column)} mixes number formats: "
                                + ", ".join(kind for kind, _ in disagreements)
                            ),
                            profile=profile,
                            provenance_path="typography.decimal_places_by_column",
                            measured="; ".join(detail for _, detail in disagreements),
                            expected="one format for every figure in the column",
                            bbox_pt=shape.bbox_pt,
                        )
                    )
                if unevaluated:
                    self.note_unchecked(
                        shape.ref,
                        f"{unevaluated} of {table.column_count} columns hold fewer "
                        "than two comparable figures",
                    )
        return findings


# --------------------------------------------------------------------------------------
# TY-007 currency notation
# --------------------------------------------------------------------------------------

#: The figure a currency marker introduces. Used to cut a candidate of the length
#: the learned pattern expects to see, spacing included.
_FIGURE: Final[re.Pattern[str]] = re.compile(r"[  ]?[\d(][\d,.  ]*\)?")


def _currency_candidates(text: str) -> Iterator[tuple[str, str]]:
    """``(marker plus figure, marker)`` for each currency-prefixed figure.

    ``currency_tokens`` returns the markers in the order they appear but not
    their positions, so the text is walked in step with them to cut the figure
    following each one.
    """
    cursor = 0
    for token in currency_tokens(text):
        index = text.find(token, cursor)
        if index < 0:  # pragma: no cover - the token came from this text
            continue
        cursor = index + len(token)
        figure = _FIGURE.match(text, cursor)
        end = figure.end() if figure is not None else min(len(text), cursor + 2)
        yield text[index:end], token


@register
class CurrencyNotation(Rule):
    """Currency notation deviating from the learned pattern.

    Measures: every currency marker immediately preceding a figure, from
    :func:`tieout.text.currency_tokens`, tested against the regular expression in
    ``typography.currency_pattern`` together with the figure it introduces, so
    that the spacing between marker and figure is part of the comparison. ``US$``,
    ``U.S.$`` and ``USD`` denote one currency but are not interchangeable in a
    house style, so :func:`tieout.text.normalise_currency` is used only to group
    the report, never to excuse a deviation. One finding per slide per offending
    marker.

    Known false positive: a figure quoted in a second currency the client
    legitimately uses is reported, because the profile learns one pattern rather
    than a set of permitted currencies.
    """

    id: ClassVar[str] = "TY-007"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "minor"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = (
        "Currency or unit notation deviates from the learned pattern"
    )
    requires: ClassVar[tuple[str, ...]] = ("typography.currency_pattern",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        expression = profile.typography.currency_pattern
        if expression is None:  # pragma: no cover - the engine skips on requires
            return self.skip("the profile does not define typography.currency_pattern")
        try:
            pattern = re.compile(expression)
        except re.error as error:
            return self.skip(
                f"typography.currency_pattern is not a valid regex: {error}"
            )

        findings: list[Finding] = []
        for slide in deck.slides:
            # Normalised marker -> first shape, one example as written, a count.
            offenders: dict[str, tuple[ShapeModel, str, int]] = {}
            for passage in _passages(slide):
                for candidate, token in _currency_candidates(passage.text):
                    if pattern.match(candidate):
                        continue
                    key = normalise_currency(token)
                    shape, example, count = offenders.get(
                        key, (passage.shape, candidate, 0)
                    )
                    offenders[key] = (shape, example, count + 1)

            for key, (shape, example, count) in sorted(offenders.items()):
                occurrences = "" if count == 1 else f", {count} times on this slide"
                findings.append(
                    self.finding(
                        where=shape.ref,
                        message=(
                            f"{example.strip()!r} does not follow the deck's currency "
                            f"notation{occurrences}"
                        ),
                        profile=profile,
                        provenance_path="typography.currency_pattern",
                        measured=f"{key}: {example.strip()!r}",
                        expected=f"matching {expression}",
                        bbox_pt=shape.bbox_pt,
                    )
                )
        return findings


# --------------------------------------------------------------------------------------
# TY-008 date format
# --------------------------------------------------------------------------------------


@register
class DateFormat(Rule):
    """A date written in a format other than the learned one.

    Measures: every date-looking substring found by
    :func:`tieout.text.find_dates`, whose ``strftime`` format is compared with
    ``typography.date_format``. One finding per slide per offending format, so a
    table of eight dates all in one wrong format is one finding.

    Known false positive: ``%d/%m/%Y`` and ``%m/%d/%Y`` are indistinguishable
    when the day is twelve or lower, and the first format listed in
    :data:`tieout.text.DATE_FORMATS` wins. A deck whose convention is the other
    one of that pair gets a finding on every such date.
    """

    id: ClassVar[str] = "TY-008"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "minor"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Date format deviates from the learned format"
    requires: ClassVar[tuple[str, ...]] = ("typography.date_format",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        convention = profile.typography.date_format
        if convention is None:  # pragma: no cover - the engine skips on requires
            return self.skip("the profile does not define typography.date_format")

        findings: list[Finding] = []
        for slide in deck.slides:
            # Format -> first shape, one example as written, and a count.
            offenders: dict[str, tuple[ShapeModel, str, int]] = {}
            for passage in _passages(slide):
                for reading in find_dates(passage.text):
                    if reading.format == convention:
                        continue
                    shape, example, count = offenders.get(
                        reading.format, (passage.shape, reading.raw, 0)
                    )
                    offenders[reading.format] = (shape, example, count + 1)

            for found, (shape, example, count) in sorted(offenders.items()):
                occurrences = "" if count == 1 else f", {count} dates on this slide"
                findings.append(
                    self.finding(
                        where=shape.ref,
                        message=(
                            f"the date {example!r} is written as {found}, not "
                            f"{convention}{occurrences}"
                        ),
                        profile=profile,
                        provenance_path="typography.date_format",
                        measured=f"{example!r} ({found})",
                        expected=convention,
                        bbox_pt=shape.bbox_pt,
                    )
                )
        return findings


# --------------------------------------------------------------------------------------
# TY-009 spelling
# --------------------------------------------------------------------------------------

#: Read once per process. The list holds around 38,000 words, and reading it per
#: shape would dominate the run time of an audit.
_WORDLIST: frozenset[str] | None = None

#: How many unknown words one finding names before it stops listing them.
_WORDS_PER_FINDING: Final[int] = 5


def _bundled_wordlist() -> frozenset[str]:
    """The bundled dictionary, loaded lazily and cached at module level."""
    global _WORDLIST
    if _WORDLIST is None:
        resource = resources.files("tieout").joinpath("data/wordlist.txt.gz")
        with resource.open("rb") as raw, gzip.open(raw, "rt", encoding="utf-8") as handle:
            _WORDLIST = frozenset(
                line.strip().casefold() for line in handle if line.strip()
            )
    return _WORDLIST


def _client_vocabulary(profile: Profile) -> frozenset[str]:
    """The client's own words: the dictionary plus every canonical term's words.

    Terminology the client has declared canonical cannot also be a spelling
    error, and TY-005 already reports its variants.
    """
    out: set[str] = {word.casefold() for word in profile.hygiene.dictionary}
    for canonical, variants in profile.typography.canon_terms.items():
        for phrase in (canonical, *variants):
            out.update(token.casefold() for token in spell_tokens(phrase))
            out.update(canon_key(phrase).split())
    return frozenset(out)


@register
class Spelling(Rule):
    """Words absent from the bundled wordlist, the client dictionary and the
    canonical terms.

    Measures: :func:`tieout.text.spell_tokens` over every passage that is not
    deck chrome -- which already discards tickers, all-caps acronyms of two to
    five characters, anything containing a digit and tokens under three
    characters -- tested in each of its :func:`tieout.text.spell_variants` forms
    against the union of the bundled wordlist, ``hygiene.dictionary`` and every
    word appearing in ``typography.canon_terms``. One finding per slide naming up
    to five unknown words. Disabled by default: a general dictionary on
    specialist prose produces more noise than any other rule in TieOut.

    Known false positive: a legitimate word the compact bundled list omits --
    British ``-ise`` inflections and finance vocabulary such as "premia" are the
    usual cases -- is reported until the client dictionary names it. Proper nouns
    are checked like any other word, so every name in the deck has to reach the
    client dictionary or the canonical terms.
    """

    id: ClassVar[str] = "TY-009"
    category: ClassVar[str] = "typography"
    severity: ClassVar[Severity] = "minor"
    default_enabled: ClassVar[bool] = False
    summary: ClassVar[str] = (
        "Spelling against a bundled wordlist plus the per-client dictionary"
    )
    #: The bundled list always ships and the client dictionary is optional, so
    #: there is no profile path whose absence should stop the rule.
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        try:
            bundled = _bundled_wordlist()
        except OSError as error:  # pragma: no cover - a broken installation
            return self.skip(f"the bundled wordlist could not be read: {error}")

        known = bundled | _client_vocabulary(profile)
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []

        for slide in deck.slides:
            unknown: dict[str, ShapeModel] = {}
            for passage in _passages(slide, furniture=furniture):
                for token in spell_tokens(passage.text):
                    if any(form in known for form in spell_variants(token)):
                        continue
                    unknown.setdefault(token, passage.shape)
            if not unknown:
                continue

            words = sorted(unknown)
            shape = unknown[words[0]]
            shown = ", ".join(repr(word) for word in words[:_WORDS_PER_FINDING])
            remainder = len(words) - _WORDS_PER_FINDING
            if remainder > 0:
                shown = f"{shown} and {remainder} more"
            findings.append(
                self.finding(
                    where=shape.ref,
                    message=(
                        f"{len(words)} unrecognised {_plural(len(words), 'word')}: "
                        f"{shown}"
                    ),
                    profile=profile,
                    provenance_path="hygiene.dictionary",
                    measured=shown,
                    expected="words in the bundled wordlist or the client dictionary",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings
