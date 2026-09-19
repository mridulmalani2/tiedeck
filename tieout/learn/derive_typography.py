"""Deriving typographic conventions.

Each convention is a dominant-value vote, but the value of the whole exercise
depends on counting at the right scope. Bullet punctuation counted per bullet
across the deck gives a number that means nothing, because a single long list
with stops on every line outvotes ten short lists without them. Counted per list
and then voted across lists, it gives the convention a reader would describe.

Every function here delegates its parsing to :mod:`tieout.text`, which is also
what the typography rules use. That shared dependency is deliberate: if the
deriver decided a column had two decimal places by one method and the rule
checked it by another, the tool would report defects on the deck it learned from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from tieout.learn.classify import (
    Classification,
    Derivation,
    ObservationClass,
    classify_categorical,
    min_support_for,
)
from tieout.learn.observe import (
    DECK,
    Observation,
    archetype_scope,
    iter_runs,
    learnable_slides,
    table_column_scope,
)
from tieout.model.deck import DeckModel
from tieout.model.furniture import Furniture
from tieout.profile.schema import TypographyProfile
from tieout.text import (
    bullet_terminal,
    capitalisation_style,
    currency_tokens,
    find_dates,
    is_numeric_placeholder,
    normalise_currency,
    parse_number,
    quote_census,
    resolve_date_convention,
)

#: Archetypes whose titles vote on the capitalisation convention.
#:
#: Section 8.3 scopes this to ``archetype:content``. Table- and chart-heavy
#: slides are included here because they are content slides that happen to carry
#: a table or a chart, and their titles are written by the same hand under the
#: same convention. Excluding them would discard a third of the evidence.
TITLE_CASE_ARCHETYPES: Final[frozenset[str]] = frozenset(
    {"content", "table_heavy", "chart_heavy"}
)


@dataclass
class TypographyDerivation:
    profile: TypographyProfile
    derivation: Derivation = field(default_factory=Derivation)


def derive_typography(
    deck: DeckModel, furniture: Furniture
) -> TypographyDerivation:
    """Derive every typographic convention the deck exhibits."""
    result = TypographyDerivation(profile=TypographyProfile())
    total = deck.slide_count

    _derive_quotes(deck, furniture, result, total)
    _derive_title_case(deck, result, total)
    _derive_bullets(deck, furniture, result, total)
    _derive_numbers(deck, result, total)
    _derive_currency(deck, furniture, result, total)
    _derive_dates(deck, furniture, result, total)
    return result


def _record(
    result: TypographyDerivation,
    path: str,
    classification: Classification,
    *,
    total_slides: int,
) -> bool:
    """Note provenance or record the key as not learned. Returns whether learned.

    Every caller here writes a field that holds exactly one value: a deck has one
    quote convention, one date format. So a *multimodal* classification -- the
    classifier's way of saying "two values are both used, and neither dominates"
    -- has no honest representation in the field, and the previous behaviour was
    to write the more common of the two as though it were the convention.

    That turned the deck's own second format into findings against it. A deck
    writing three dates as "15 September 2026" and five as "September 2026"
    learned ``%B %Y`` and then reported the three, on the very deck the
    convention had been read from. The user saw three defects where the truth was
    that TieOut could not tell which spelling was the house one.

    Recording it as not learned is the answer the rest of the design already
    gives for evidence that does not settle a question: the rule that reads the
    field does not run, and ``not_learned`` says why, so the silence cannot be
    mistaken for a pass. Where a field *can* hold a set -- the approved typefaces,
    the currency pattern -- the deriver reads ``classification.allowed`` itself
    and does not come through here.
    """
    if not classification.learned:
        result.derivation.unlearned(path, classification.reason)
        return False
    if classification.observation_class is ObservationClass.MULTIMODAL:
        modes = ", ".join(str(value) for value in classification.allowed)
        result.derivation.unlearned(
            path,
            f"the deck uses {len(classification.allowed)} of these consistently "
            f"({modes}) and none dominates, so there is no single house "
            f"convention to enforce",
        )
        return False
    result.derivation.note_from(path, classification, total_slides=total_slides)
    return True


# --------------------------------------------------------------------------------------


def _derive_quotes(
    deck: DeckModel,
    furniture: Furniture,
    result: TypographyDerivation,
    total_slides: int,
) -> None:
    """Weighted by glyph count, because one convention is used more than voted for.

    A deck with three hundred curly apostrophes and no straight ones is
    unambiguous; a deck with three hundred curly and two straight has a
    convention plus two defects, and the weighting is what separates those from a
    deck that genuinely mixes both.
    """
    observations: list[Observation] = []
    for context in iter_runs(deck, furniture):
        census = quote_census(context.run.text)
        if census.curly:
            observations.append(
                Observation("quotes", DECK, "curly", context.slide.index, census.curly)
            )
        if census.straight:
            observations.append(
                Observation(
                    "quotes", DECK, "straight", context.slide.index, census.straight
                )
            )

    classification = classify_categorical("quotes", DECK, observations)
    if _record(result, "typography.quotes", classification, total_slides=total_slides):
        value = str(classification.value)
        if value in ("curly", "straight"):
            result.profile.quotes = value  # type: ignore[assignment]


def _derive_title_case(
    deck: DeckModel, result: TypographyDerivation, total_slides: int
) -> None:
    scope = archetype_scope("content")
    observations: list[Observation] = []
    undecidable = 0
    for slide in learnable_slides(deck):
        if slide.archetype not in TITLE_CASE_ARCHETYPES:
            continue
        title = slide.title_text
        if not title:
            continue
        style = capitalisation_style(title)
        if style is None:
            undecidable += 1
            continue
        observations.append(Observation("title_case", scope, style, slide.index))

    classification = classify_categorical("title_case", scope, observations)
    if undecidable and not classification.learned:
        classification.reason += (
            f"; a further {undecidable} title"
            f"{'s were' if undecidable != 1 else ' was'} too short to classify"
        )
    if _record(
        result, "typography.title_case", classification, total_slides=total_slides
    ):
        value = str(classification.value)
        if value in ("sentence", "title", "upper"):
            result.profile.title_case = value  # type: ignore[assignment]


def _derive_bullets(
    deck: DeckModel,
    furniture: Furniture,
    result: TypographyDerivation,
    total_slides: int,
) -> None:
    """Per list first, then across lists.

    Section 8.3: "scope is per list, then dominant across lists". A list is one
    text frame, which is how a reader perceives it.
    """
    per_list: dict[tuple[int, int], list[str]] = {}
    for context in iter_runs(deck, furniture):
        if not context.paragraph.is_bulleted or context.cell is not None:
            continue
        key = (context.slide.index, context.shape.ref.uid)
        per_list.setdefault(key, []).append(bullet_terminal(context.paragraph.text))

    observations: list[Observation] = []
    for (slide_index, uid), terminals in sorted(per_list.items()):
        if not terminals:
            continue
        counts: dict[str, int] = {}
        for terminal in terminals:
            counts[terminal] = counts.get(terminal, 0) + 1
        dominant = max(sorted(counts), key=lambda k: counts[k])
        observations.append(
            Observation(
                "bullet_terminal",
                DECK,
                dominant,
                slide_index,
                1.0,
                uid=uid,
            )
        )

    classification = classify_categorical("bullet_terminal", DECK, observations)
    if _record(
        result,
        "typography.bullet_terminal_punctuation",
        classification,
        total_slides=total_slides,
    ):
        value = str(classification.value)
        if value in ("none", "period", "semicolon"):
            result.profile.bullet_terminal_punctuation = value  # type: ignore[assignment]
        bullets = sum(len(v) for v in per_list.values())
        result.derivation.note(
            "typography.bullet_terminal_punctuation",
            f"{classification.top_share:.0%} of {len(observations)} bulleted lists "
            f"({bullets} bullets in total) across {total_slides} slides end in "
            f"{value}",
            classification.confidence,
        )


def _derive_numbers(
    deck: DeckModel, result: TypographyDerivation, total_slides: int
) -> None:
    """Number conventions, scoped to the column and then voted deck-wide.

    Decimal places genuinely belong to the column: a revenue column at zero
    places and a margin column at one place is correct, not inconsistent. So
    decimals are emitted as the *policy* ``consistent_within_column`` rather than
    as a number, which is what TY-006 enforces. The thousands separator and the
    negative style, by contrast, are deck-wide conventions.
    """
    separators: list[Observation] = []
    negatives: list[Observation] = []
    consistent_columns = 0
    inconsistent_columns = 0

    for slide in learnable_slides(deck):
        for shape in slide.tables:
            table = shape.table
            if table is None:
                continue
            for column, cells in table.iter_columns(skip_header=True):
                scope = table_column_scope(slide.index, shape.ref.uid, column)
                decimals: set[int] = set()
                for cell in cells:
                    text = cell.text
                    if not text or is_numeric_placeholder(text):
                        continue
                    reading = parse_number(text)
                    if reading is None:
                        continue
                    decimals.add(reading.decimals)
                    if reading.thousands_separator is not None:
                        separators.append(
                            Observation(
                                "thousands_separator",
                                DECK,
                                reading.thousands_separator,
                                slide.index,
                            )
                        )
                    elif reading.value >= 1000:
                        separators.append(
                            Observation(
                                "thousands_separator", DECK, "none", slide.index
                            )
                        )
                    if reading.negative_style is not None:
                        negatives.append(
                            Observation(
                                "negative_style",
                                DECK,
                                reading.negative_style,
                                slide.index,
                            )
                        )
                if not decimals:
                    continue
                if len(decimals) == 1:
                    consistent_columns += 1
                else:
                    inconsistent_columns += 1
                del scope

    total_columns = consistent_columns + inconsistent_columns
    if total_columns >= min_support_for(DECK) and inconsistent_columns == 0:
        result.profile.decimal_places_by_column = "consistent_within_column"
        result.derivation.note(
            "typography.decimal_places_by_column",
            f"all {consistent_columns} numeric table columns across "
            f"{total_slides} slides use one decimal precision throughout",
            "high",
        )
    elif total_columns == 0:
        result.derivation.unlearned(
            "typography.decimal_places_by_column",
            "the reference deck contains no numeric table columns",
        )
    else:
        result.derivation.unlearned(
            "typography.decimal_places_by_column",
            f"{inconsistent_columns} of {total_columns} numeric columns already mix "
            f"decimal precisions, so consistency is not this deck's convention",
        )

    separator_class = classify_categorical("thousands_separator", DECK, separators)
    if _record(
        result,
        "typography.thousands_separator",
        separator_class,
        total_slides=total_slides,
    ):
        value = str(separator_class.value)
        result.profile.thousands_separator = None if value == "none" else value

    negative_class = classify_categorical("negative_style", DECK, negatives)
    if _record(
        result, "typography.negative_style", negative_class, total_slides=total_slides
    ):
        value = str(negative_class.value)
        if value in ("parentheses", "minus"):
            result.profile.negative_style = value  # type: ignore[assignment]


def _derive_currency(
    deck: DeckModel,
    furniture: Furniture,
    result: TypographyDerivation,
    total_slides: int,
) -> None:
    """The currency marker, normalised before clustering.

    ``US$`` and ``U.S.$`` denote the same currency but are not interchangeable in
    a house style, so normalisation collapses case and spacing without merging
    genuinely different markers.
    """
    observations: list[Observation] = []
    surface: dict[str, str] = {}
    for context in iter_runs(deck, furniture, include_furniture=True):
        for token in currency_tokens(context.run.text):
            key = normalise_currency(token)
            surface.setdefault(key, token)
            observations.append(
                Observation("currency", DECK, key, context.slide.index)
            )
    for slide in learnable_slides(deck):
        for shape in slide.tables:
            if shape.table is None:
                continue
            for cell in shape.table.cells:
                for token in currency_tokens(cell.text):
                    key = normalise_currency(token)
                    surface.setdefault(key, token)
                    observations.append(
                        Observation("currency", DECK, key, slide.index)
                    )

    classification = classify_categorical("currency", DECK, observations)
    if not classification.learned:
        result.derivation.unlearned(
            "typography.currency_pattern", classification.reason
        )
        return

    allowed = classification.allowed or [str(classification.value)]
    literals = sorted({surface.get(key, key) for key in allowed})
    import re as _re

    pattern = "^(" + "|".join(_re.escape(literal) for literal in literals) + r")\s?[\d(]"
    result.profile.currency_pattern = pattern
    result.derivation.note_from(
        "typography.currency_pattern", classification, total_slides=total_slides
    )
    result.derivation.note(
        "typography.currency_pattern",
        f"{classification.support} currency-prefixed figures across "
        f"{len(set(classification.slides))} slides, written as "
        + ", ".join(repr(literal) for literal in literals),
        classification.confidence,
    )


def _derive_dates(
    deck: DeckModel,
    furniture: Furniture,
    result: TypographyDerivation,
    total_slides: int,
) -> None:
    """The date format, inferred by trying a fixed list of strptime patterns.

    The deck is read twice on purpose. ``%d/%m/%Y`` and ``%m/%d/%Y`` cannot be
    told apart when the day is twelve or lower, so resolving each date on its
    own split one consistent convention across two formats -- and a US deck,
    whose dates are month-first, taught this deriver both of them. The first
    pass asks the whole deck which ordering it uses, from the dates that
    disambiguate themselves; the second reads every date under that answer.
    """
    passages: list[str] = [
        context.run.text
        for context in iter_runs(deck, furniture, include_furniture=True)
    ]
    for slide in learnable_slides(deck):
        for shape in slide.tables:
            if shape.table is None:
                continue
            passages.extend(cell.text for cell in shape.table.cells)

    convention = resolve_date_convention(passages)

    observations: list[Observation] = []
    for context in iter_runs(deck, furniture, include_furniture=True):
        for reading in find_dates(context.run.text, prefer=convention):
            observations.append(
                Observation("date_format", DECK, reading.format, context.slide.index)
            )
    for slide in learnable_slides(deck):
        for shape in slide.tables:
            if shape.table is None:
                continue
            for cell in shape.table.cells:
                for reading in find_dates(cell.text, prefer=convention):
                    observations.append(
                        Observation("date_format", DECK, reading.format, slide.index)
                    )

    classification = classify_categorical("date_format", DECK, observations)
    if _record(
        result, "typography.date_format", classification, total_slides=total_slides
    ):
        result.profile.date_format = str(classification.value)
