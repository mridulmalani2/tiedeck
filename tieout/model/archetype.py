"""Deterministic slide archetype classification.

Archetypes are what let the learning engine work without interrogating the user
about every exception. "The logo is at x=872" is false for a deck; "the logo is at
x=872 on content slides and centred on section dividers" is true, and the only way
to derive the second is to know which slides are which.

No machine learning. A cascade of ordered, explainable predicates, each of which
records why it fired, so a user who disagrees can see the reasoning and correct
the assignment in the profile.

Anything classified ``unknown`` is excluded from learning -- a slide TieOut cannot
categorise must not be allowed to pollute a derived rule -- but is still checked
against deck-wide rules.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from tieout.model.deck import TITLE_BAND_SHARE, DeckModel, ShapeModel, SlideModel

#: Every archetype TieOut assigns.
ARCHETYPES: Final[tuple[str, ...]] = (
    "title",
    "section_divider",
    "agenda",
    "content",
    "full_bleed",
    "table_heavy",
    "chart_heavy",
    "appendix_divider",
    "disclaimer",
    "unknown",
)

#: Archetypes that carry body content and are therefore comparable to each other
#: for margin and grid derivation.
CONTENT_ARCHETYPES: Final[frozenset[str]] = frozenset(
    {"content", "table_heavy", "chart_heavy"}
)

#: Archetypes with little or no body content, where margin derivation is
#: meaningless and a missing page number is usually intentional.
SPARSE_ARCHETYPES: Final[frozenset[str]] = frozenset(
    {"title", "section_divider", "appendix_divider", "full_bleed"}
)

_AGENDA_RE: Final[re.Pattern[str]] = re.compile(
    r"^(table\s+of\s+contents|contents|agenda|today'?s\s+agenda|discussion\s+"
    r"(materials|topics)|overview\s+of\s+(today|discussion))\s*$",
    re.IGNORECASE,
)
_APPENDIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^appendix(\s+[ivxlcdm\d]+)?\s*[:.\-]?\s*(.{0,40})$", re.IGNORECASE
)
_DISCLAIMER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(disclaimer|disclaimers|disclosures?|important\s+(notice|information|"
    r"disclosures?)|notice\s+to\s+recipients?|legal\s+notice|confidentiality\s+"
    r"(notice|statement))\s*$",
    re.IGNORECASE,
)
_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^(section|part)\s+([ivxlcdm]+|\d+|[a-z])\s*[:.\-]?\s*(.{0,60})$", re.IGNORECASE
)

#: ``p:sldLayout@type`` values that name the archetype directly.
_LAYOUT_TYPE_HINTS: Final[dict[str, str]] = {
    "title": "title",
    "secHead": "section_divider",
    "blank": "full_bleed",
    "tbl": "table_heavy",
    "chart": "chart_heavy",
    "txAndChart": "chart_heavy",
    "chartAndTx": "chart_heavy",
}

#: Layout name fragments, matched case-insensitively. Designers name layouts for
#: their own benefit, and those names are the clearest statement of intent in the
#: whole file.
_LAYOUT_NAME_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("title slide", "title"),
    ("cover", "title"),
    ("section header", "section_divider"),
    ("section divider", "section_divider"),
    ("divider", "section_divider"),
    ("agenda", "agenda"),
    ("table of contents", "agenda"),
    ("contents", "agenda"),
    ("appendix", "appendix_divider"),
    ("disclaimer", "disclaimer"),
    ("disclosure", "disclaimer"),
    ("full bleed", "full_bleed"),
    ("full-bleed", "full_bleed"),
    ("image only", "full_bleed"),
)

# Thresholds. Every one is a judgement call; they are collected here so a
# disagreement is a one-line change rather than an archaeology exercise.
_SPARSE_TEXT_CHARS: Final[int] = 200
_DIVIDER_MAX_SHAPES: Final[int] = 8
_DIVIDER_MIN_TITLE_PT: Final[float] = 18.0
_FULL_BLEED_AREA_SHARE: Final[float] = 0.80
_TABLE_AREA_SHARE: Final[float] = 0.30
_TABLE_TEXT_SHARE: Final[float] = 0.50
_CHART_AREA_SHARE: Final[float] = 0.25
_DISCLAIMER_MIN_CHARS: Final[int] = 1200
_DISCLAIMER_MAX_FONT_PT: Final[float] = 9.5
_AGENDA_MAX_CHARS: Final[int] = 900

#: Longest standalone string still read as a divider's kicker. The section
#: pattern is loose enough to match prose -- "Part 3 of the agreement governs the
#: escrow" -- so the claim is bounded to something short enough to be a label.
_SECTION_MARK_MAX_CHARS: Final[int] = 40


@dataclass(frozen=True, slots=True)
class ArchetypeResult:
    """A classification with its confidence and the reasons that produced it."""

    archetype: str
    confidence: str  # high | medium | low
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Signals:
    """Measured facts about one slide, computed once and shared by all predicates."""

    slide: SlideModel
    index: int
    slide_count: int
    title_text: str
    title_font_pt: float | None
    #: A "SECTION 03" kicker found in the slide's upper half, if any.
    section_mark: str
    text_chars: int
    shape_count: int
    leaf_count: int
    largest_area_share: float
    largest_shape: ShapeModel | None
    table_area_share: float
    table_text_share: float
    chart_area_share: float
    has_table: bool
    has_chart: bool
    has_picture: bool
    layout_name: str
    layout_type: str
    placeholder_signature: tuple[str, ...]
    dominant_body_font_pt: float | None
    centred_large_text: bool
    previous_archetype: str | None


def classify_deck(deck: DeckModel) -> dict[int, ArchetypeResult]:
    """Classify every slide, assigning the result onto each :class:`SlideModel`.

    Classification is sequential because one signal -- "the slide after a section
    divider" -- depends on the previous slide's assignment.
    """
    results: dict[int, ArchetypeResult] = {}
    previous: str | None = None
    for slide in deck.slides:
        signals = _measure(slide, deck, previous)
        result = _classify(signals)
        results[slide.index] = result
        slide.archetype = result.archetype
        slide.archetype_confidence = result.confidence
        slide.archetype_reasons = result.reasons
        previous = result.archetype
    return results


def classify_slide(
    slide: SlideModel, deck: DeckModel, previous_archetype: str | None = None
) -> ArchetypeResult:
    """Classify one slide in isolation. Used by tests and by the report."""
    return _classify(_measure(slide, deck, previous_archetype))


def _measure(
    slide: SlideModel, deck: DeckModel, previous_archetype: str | None
) -> _Signals:
    leaves = list(slide.leaf_shapes())
    slide_area = slide.area_pt2 or 1.0

    largest = max(leaves, key=lambda s: s.area_pt2, default=None)
    table_area = sum(s.area_pt2 for s in slide.tables)
    chart_area = sum(s.area_pt2 for s in slide.charts)

    total_chars = slide.text_length or 1
    table_chars = sum(s.char_count for s in slide.tables)

    title = slide.title_shape
    title_pt = (
        title.effective_font.size_pt
        if title is not None and title.effective_font is not None
        else None
    )

    return _Signals(
        slide=slide,
        index=slide.index,
        slide_count=deck.slide_count,
        title_text=slide.title_text,
        title_font_pt=title_pt,
        section_mark=_section_mark(slide),
        text_chars=slide.text_length,
        shape_count=len(slide.shapes),
        leaf_count=len(leaves),
        largest_area_share=(largest.area_pt2 / slide_area) if largest else 0.0,
        largest_shape=largest,
        table_area_share=table_area / slide_area,
        table_text_share=table_chars / total_chars,
        chart_area_share=chart_area / slide_area,
        has_table=bool(slide.tables),
        has_chart=bool(slide.charts),
        has_picture=bool(slide.pictures),
        layout_name=(slide.layout_name or "").strip(),
        layout_type=(slide.layout_type or "").strip(),
        placeholder_signature=slide.placeholder_signature,
        dominant_body_font_pt=_dominant_body_font_pt(slide),
        centred_large_text=_has_centred_large_text(slide),
        previous_archetype=previous_archetype,
    )


def _section_mark(slide: SlideModel) -> str:
    """A divider's kicker, wherever on the slide it is set.

    A section divider announces itself with a label -- SECTION 03, PART II --
    and the headline beside it is an ordinary heading. Testing only the title
    therefore misses the announcement on every divider that has both, which is
    most of them: the client deck sets SECTION 03 above "Financial Performance &
    Valuation" and was filed as a content slide, so it was measured against
    content-slide margins, the content logo box and the content capitalisation
    convention.

    Bounded to a short standalone string in the upper half. The pattern matches
    the start of a sentence as readily as a label, and prose that opens "Part 3
    of the agreement" is not a divider.
    """
    for shape in slide.text_shapes:
        if shape.top_pt >= slide.height_pt * TITLE_BAND_SHARE:
            continue
        text = shape.text.strip()
        if not text or len(text) > _SECTION_MARK_MAX_CHARS:
            continue
        if _SECTION_RE.match(text):
            return text
    return ""


def _dominant_body_font_pt(slide: SlideModel) -> float | None:
    """The font size covering the most characters outside the title."""
    title = slide.title_shape
    title_id = title.ref.uid if title else None
    weights: dict[float, int] = {}
    for shape in slide.leaf_shapes():
        if shape.ref.uid == title_id:
            continue
        for paragraph in shape.all_paragraphs:
            for run in paragraph.runs:
                size = run.font.size_pt
                if size is None or not run.text.strip():
                    continue
                weights[size] = weights.get(size, 0) + len(run.text)
    if not weights:
        return None
    return max(weights.items(), key=lambda item: item[1])[0]


def _has_centred_large_text(slide: SlideModel) -> bool:
    """A single large, unbulleted text shape in the middle band of the slide.

    This is the visual signature of a section divider, and it is more reliable
    than the layout name because designers reuse a content layout for dividers
    surprisingly often.

    Bulleted shapes are excluded deliberately. A body placeholder holding two
    short bullets at 28pt sits in the middle band and is the right size, and
    without this exclusion every sparse content slide in a deck is misread as a
    divider.
    """
    if len(slide.text_shapes) > 3:
        return False
    band_top = slide.height_pt * 0.2
    band_bottom = slide.height_pt * 0.8
    for shape in slide.text_shapes:
        font = shape.effective_font
        if font is None or (font.size_pt or 0) < _DIVIDER_MIN_TITLE_PT:
            continue
        if any(p.is_bulleted for p in shape.text_frame_paragraphs):
            continue
        centre = shape.centre_y_pt
        if band_top <= centre <= band_bottom and shape.char_count <= 120:
            return True
    return False


# --------------------------------------------------------------------------------------
# The cascade
# --------------------------------------------------------------------------------------


def _classify(s: _Signals) -> ArchetypeResult:
    """Run the predicate cascade. First match wins; order encodes specificity."""
    for predicate in _CASCADE:
        result = predicate(s)
        if result is not None:
            return result
    return ArchetypeResult("unknown", "low", ("no predicate matched",))


def _by_title_agenda(s: _Signals) -> ArchetypeResult | None:
    if s.title_text and _AGENDA_RE.match(s.title_text.strip()):
        if s.text_chars <= _AGENDA_MAX_CHARS:
            return ArchetypeResult(
                "agenda", "high", (f"title matches agenda pattern: {s.title_text!r}",)
            )
        return ArchetypeResult(
            "content",
            "medium",
            (
                f"title matches agenda pattern: {s.title_text!r}",
                f"but {s.text_chars} characters of body text is too dense for an agenda",
            ),
        )
    return None


def _by_title_disclaimer(s: _Signals) -> ArchetypeResult | None:
    if s.title_text and _DISCLAIMER_RE.match(s.title_text.strip()):
        return ArchetypeResult(
            "disclaimer", "high", (f"title matches disclaimer pattern: {s.title_text!r}",)
        )
    return None


def _by_dense_small_text(s: _Signals) -> ArchetypeResult | None:
    """A wall of tiny type is a disclaimer whatever its title says."""
    if (
        s.text_chars >= _DISCLAIMER_MIN_CHARS
        and s.dominant_body_font_pt is not None
        and s.dominant_body_font_pt <= _DISCLAIMER_MAX_FONT_PT
        and not s.has_table
    ):
        return ArchetypeResult(
            "disclaimer",
            "medium",
            (
                f"{s.text_chars} characters at a dominant {s.dominant_body_font_pt}pt",
                "dense small type with no table is the disclaimer signature",
            ),
        )
    return None


def _by_title_appendix(s: _Signals) -> ArchetypeResult | None:
    match = s.title_text and _APPENDIX_RE.match(s.title_text.strip())
    if not match:
        return None
    # "Appendix" alone on a sparse slide is a divider. "Appendix A -- Comparable
    # Companies" over a dense table is an appendix content slide.
    if s.text_chars <= _SPARSE_TEXT_CHARS and not (s.has_table or s.has_chart):
        return ArchetypeResult(
            "appendix_divider",
            "high",
            (
                f"title matches appendix pattern: {s.title_text!r}",
                f"sparse slide, {s.text_chars} characters of text",
            ),
        )
    return None


def _by_first_slide_title(s: _Signals) -> ArchetypeResult | None:
    if s.index != 1:
        return None
    hints: list[str] = ["slide 1"]
    confident = False
    if s.layout_type == "title" or "ctrTitle" in s.placeholder_signature:
        hints.append("title layout or centre-title placeholder")
        confident = True
    if _layout_name_hint(s.layout_name) == "title":
        hints.append(f"layout named {s.layout_name!r}")
        confident = True
    if s.text_chars <= 600:
        hints.append(f"only {s.text_chars} characters of text")
    if confident or s.text_chars <= 600:
        return ArchetypeResult("title", "high" if confident else "medium", tuple(hints))
    return None


def _by_layout_type(s: _Signals) -> ArchetypeResult | None:
    """Trust an explicit ``secHead`` or ``title`` layout type anywhere in the deck."""
    hinted = _LAYOUT_TYPE_HINTS.get(s.layout_type)
    if hinted in ("section_divider", "title"):
        if hinted == "title" and s.index != 1:
            # A title layout reused mid-deck is a divider, not a second cover.
            return ArchetypeResult(
                "section_divider",
                "medium",
                (
                    f"layout type {s.layout_type!r} is a title layout reused at "
                    f"slide {s.index}",
                ),
            )
        return ArchetypeResult(
            hinted, "high", (f"layout type attribute is {s.layout_type!r}",)
        )
    return None


def _by_layout_name(s: _Signals) -> ArchetypeResult | None:
    hinted = _layout_name_hint(s.layout_name)
    if hinted is None:
        return None
    # A named appendix or disclaimer layout is trustworthy. A named divider layout
    # still has to look sparse, because designers reuse layouts.
    if hinted in ("appendix_divider", "disclaimer", "agenda"):
        return ArchetypeResult(hinted, "high", (f"layout named {s.layout_name!r}",))
    if hinted == "section_divider" and s.text_chars <= _SPARSE_TEXT_CHARS * 3:
        return ArchetypeResult(
            hinted,
            "high",
            (f"layout named {s.layout_name!r}", f"{s.text_chars} characters of text"),
        )
    if hinted == "title" and s.index == 1:
        return ArchetypeResult(hinted, "high", (f"layout named {s.layout_name!r}",))
    return None


def _by_section_title_pattern(s: _Signals) -> ArchetypeResult | None:
    if s.text_chars > _SPARSE_TEXT_CHARS * 2:
        return None
    title = s.title_text.strip()
    if title and _SECTION_RE.match(title):
        return ArchetypeResult(
            "section_divider",
            "high",
            (f"title matches section pattern: {title!r}",),
        )
    if s.section_mark:
        return ArchetypeResult(
            "section_divider",
            "high",
            (f"slide is marked {s.section_mark!r} above its heading",),
        )
    return None


def _by_full_bleed(s: _Signals) -> ArchetypeResult | None:
    """One shape covering most of the canvas, carrying the slide on its own."""
    if s.largest_area_share < _FULL_BLEED_AREA_SHARE or s.largest_shape is None:
        return None
    kind = s.largest_shape.kind
    if kind not in ("picture", "autoshape", "freeform", "media"):
        return None
    if s.has_table or s.has_chart:
        return None
    if kind == "autoshape" and s.text_chars > _SPARSE_TEXT_CHARS:
        # A large text box full of prose is a content slide, not a full bleed.
        return None
    return ArchetypeResult(
        "full_bleed",
        "high" if kind == "picture" else "medium",
        (
            f"largest shape covers {s.largest_area_share:.0%} of the canvas",
            f"largest shape is a {kind}",
        ),
    )


def _by_sparse_divider(s: _Signals) -> ArchetypeResult | None:
    """Little text, few shapes, one big centred line."""
    if s.text_chars > _SPARSE_TEXT_CHARS:
        return None
    if s.leaf_count > _DIVIDER_MAX_SHAPES:
        return None
    if s.has_table or s.has_chart:
        return None
    if not s.centred_large_text:
        return None
    return ArchetypeResult(
        "section_divider",
        "high",
        (
            f"{s.text_chars} characters across {s.leaf_count} shapes",
            "single large text shape in the middle band of the slide",
        ),
    )


def _by_table_heavy(s: _Signals) -> ArchetypeResult | None:
    if not s.has_table:
        return None
    if (
        s.table_area_share >= _TABLE_AREA_SHARE
        or s.table_text_share >= _TABLE_TEXT_SHARE
    ):
        return ArchetypeResult(
            "table_heavy",
            "high",
            (
                f"tables cover {s.table_area_share:.0%} of the canvas",
                f"tables hold {s.table_text_share:.0%} of the slide's text",
            ),
        )
    return None


def _by_chart_heavy(s: _Signals) -> ArchetypeResult | None:
    if not s.has_chart:
        return None
    if s.chart_area_share >= _CHART_AREA_SHARE:
        return ArchetypeResult(
            "chart_heavy",
            "high",
            (f"charts cover {s.chart_area_share:.0%} of the canvas",),
        )
    return None


def _by_position_after_divider(s: _Signals) -> ArchetypeResult | None:
    """The slide after a divider, with real text, is the section's first content
    slide. This only fires when nothing more specific matched."""
    if s.previous_archetype not in ("section_divider", "appendix_divider"):
        return None
    if s.text_chars <= _SPARSE_TEXT_CHARS:
        return None
    return ArchetypeResult(
        "content",
        "medium",
        (
            f"follows a {s.previous_archetype}",
            f"{s.text_chars} characters of body text",
        ),
    )


def _by_placeholder_signature(s: _Signals) -> ArchetypeResult | None:
    """A title placeholder plus a body placeholder carrying text is a content
    slide, whatever its density.

    The placeholder signature is the designer's own statement of what the slide
    is for, and it is the signal that keeps a sparse but legitimate content slide
    (three bullets and a chart) out of ``unknown``.
    """
    signature = set(s.placeholder_signature)
    has_title = bool(signature & {"title", "ctrTitle"})
    has_body = bool(signature & {"body", "obj", "subTitle", "tbl", "chart"})
    if has_title and has_body and s.text_chars > 0:
        return ArchetypeResult(
            "content",
            "medium",
            (
                f"placeholder signature {s.placeholder_signature}",
                "a title and a populated body placeholder is a content slide",
            ),
        )
    return None


def _by_default_content(s: _Signals) -> ArchetypeResult | None:
    """Anything with a title and body text is a content slide."""
    if s.text_chars > _SPARSE_TEXT_CHARS and s.leaf_count >= 2:
        return ArchetypeResult(
            "content",
            "high" if s.title_text else "medium",
            (
                f"{s.text_chars} characters across {s.leaf_count} shapes",
                "title present" if s.title_text else "no title shape found",
            ),
        )
    return None


def _by_empty(s: _Signals) -> ArchetypeResult | None:
    if s.leaf_count == 0:
        return ArchetypeResult("unknown", "high", ("slide has no shapes",))
    if s.text_chars == 0 and not (s.has_table or s.has_chart or s.has_picture):
        return ArchetypeResult(
            "unknown", "high", ("slide has shapes but no text, table, chart or image",)
        )
    return None


def _layout_name_hint(layout_name: str) -> str | None:
    lowered = layout_name.lower()
    for fragment, archetype in _LAYOUT_NAME_HINTS:
        if fragment in lowered:
            return archetype
    return None


#: The cascade, most specific first. Title-text patterns lead because an explicit
#: "Agenda" or "Disclaimer" is a statement of intent no geometric heuristic should
#: be allowed to override.
_CASCADE: Final[tuple[Callable[[_Signals], ArchetypeResult | None], ...]] = (
    _by_empty,
    _by_title_agenda,
    _by_title_disclaimer,
    _by_title_appendix,
    _by_dense_small_text,
    _by_first_slide_title,
    _by_section_title_pattern,
    _by_layout_type,
    _by_layout_name,
    _by_full_bleed,
    _by_sparse_divider,
    _by_table_heavy,
    _by_chart_heavy,
    _by_position_after_divider,
    _by_placeholder_signature,
    _by_default_content,
)
