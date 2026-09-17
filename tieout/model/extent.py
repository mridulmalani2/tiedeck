"""Where a shape's ink actually lands, as distinct from where its box sits.

PowerPoint stores a text box's *frame*, not its glyphs. A 6pt wordmark left
aligned in a 165pt box occupies about a third of it, and the rest is empty. Any
geometric rule that measures the frame therefore reports overflow, overlap and
gutters that no reader can see -- which is the single largest source of false
positives on a real deck, where generously sized text boxes are the norm.

This module narrows a text shape's box to a rectangle that **provably contains**
its ink, and never to anything smaller. It works in two regimes, run by run.

**Measured.** Where the run's typeface is installed, or a metric-compatible
substitute is -- Carlito for Calibri, Caladea for Cambria, Liberation Sans for
Arial -- :mod:`tieout.model.fonts` returns the real advance width. Metric
compatibility means exactly that the advances match, so this is a measurement
rather than an approximation, and it narrows a frame far more tightly than any
bound can.

**Bounded.** Where no honest measurement is available, an upper bound stands in:

* :data:`MAX_ADVANCE_EM` bounds the width of one character. No widely used
  proportional Latin face advances more than about 0.95 em on its widest glyph,
  and PowerPoint's letter spacing (``spc``) adds at most a fifth of an em in
  practice, so 1.15 em per character cannot be exceeded by real text.
* :data:`MAX_LINE_EM` bounds one line's height against the largest run on it.

The fallback is not a detail: TieOut runs on servers that have never had
Calibri installed, and cannot ship it, so the bound is what most deployments
will actually use. Being an upper bound it can only ever *fail to narrow* -- it
cannot shrink a box past its ink and hide a real defect -- and a mixture of
measured and bounded runs on one line is still an upper bound, because each
term is. Where the narrowed box still reaches the edge of the frame on an axis,
that axis is left at the frame, and the caller measures what the file says.

The second thing this module names is a **deliberate bleed**: an untexted shape
crossing a slide edge, which is how a cover graphic is drawn and not how a
mistake looks. Nothing in the file records the designer's intent, so the test is
structural -- no text, no content, part in and part out, sitting behind the
content it decorates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

from tieout.model.deck import (
    ALIGN_CENTRE,
    ALIGN_RIGHT,
    SPREAD_ALIGNMENTS,
    ShapeModel,
    SlideModel,
    TextParagraph,
    TextRun,
)
from tieout.model.fonts import measure_text, resolve_font_path

#: Upper bound on one character's advance, in ems. The widest glyph in a bold
#: proportional Latin face runs about 0.95 em; PowerPoint's ``spc`` tracking
#: adds up to roughly 0.2 em on a letterspaced wordmark. Anything above this is
#: not text a deck contains.
MAX_ADVANCE_EM: Final[float] = 1.15

#: Upper bound on one line's height as a multiple of its largest run. Single
#: spaced Latin text sets at about 1.2; 1.5 covers a face with unusually deep
#: descenders and PowerPoint's own rounding.
MAX_LINE_EM: Final[float] = 1.5

#: Used when a run carries no resolved size. Larger than any body text, so the
#: bound stays an over-estimate rather than becoming a guess that could shrink
#: a box past its ink.
ASSUMED_SIZE_PT: Final[float] = 54.0

#: A shape must have at least this much of its area on the canvas, and this much
#: off it, to read as a bleed rather than as a stray or a full-bleed background.
BLEED_MIN_INSIDE_SHARE: Final[float] = 0.15
BLEED_MIN_OUTSIDE_SHARE: Final[float] = 0.02

#: A bleed sits behind the content it decorates. A shape in the back this share
#: of the slide's z-order qualifies.
BLEED_BACK_Z_SHARE: Final[float] = 0.5


@dataclass(frozen=True, slots=True)
class InkExtent:
    """A rectangle proven to contain a shape's ink, and what was narrowed."""

    left: float
    top: float
    width: float
    height: float
    #: True when the bound came in inside the frame on that axis. False means
    #: the frame itself is the only honest answer there.
    narrowed_x: bool
    narrowed_y: bool
    #: True when every run was measured in a real typeface. False when any run
    #: took the bound instead, in which case the rectangle is sound but loose,
    #: and a finding computed from it is weaker than one from a measurement.
    measured: bool = False

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.left, self.top, self.width, self.height)

    @property
    def narrowed(self) -> bool:
        return self.narrowed_x or self.narrowed_y


def _run_size(run: TextRun) -> float:
    size = run.font.size_pt if run.font is not None else None
    return float(size) if size and size > 0 else ASSUMED_SIZE_PT


def _run_font_path(run: TextRun) -> str | None:
    """The file to measure this run in, where one honestly stands for it."""
    font = run.font
    if font is None or not font.name:
        return None
    return resolve_font_path(font.name, bold=bool(font.bold), italic=bool(font.italic))


def _text_width(run: TextRun, text: str) -> float:
    """``text``'s advance in ``run``'s face, measured where it is available.

    Takes the text rather than reading ``run.text`` so that one word of a run
    can be measured on its own, which is what wrapping needs.
    """
    if not text:
        return 0.0
    size_pt = _run_size(run)
    path = _run_font_path(run)
    if path is not None:
        try:
            return measure_text(path, text, size_pt)[0]
        except OSError:  # pragma: no cover - a font file that stops being readable
            pass
    return len(text) * size_pt * MAX_ADVANCE_EM


def _run_width(run: TextRun) -> float:
    """The run's advance, measured where the face is available and bounded where not."""
    return _text_width(run, run.text)


def _run_line_height(run: TextRun) -> float:
    """One line's height for this run, measured where possible and bounded where not."""
    size_pt = _run_size(run)
    path = _run_font_path(run)
    if path is not None:
        try:
            return measure_text(path, run.text or "M", size_pt)[1]
        except OSError:  # pragma: no cover - a font file that stops being readable
            pass
    return size_pt * MAX_LINE_EM


def paragraph_available_width(available: float, paragraph: TextParagraph) -> float:
    """The width one paragraph actually gets inside a frame's available width.

    A bulleted or indented paragraph does not get the whole frame. ``marL``
    moves every line of it in, and ``indent`` moves the first line relative to
    that -- negative for the hanging indent a bullet uses, positive for a
    first-line indent.

    The narrowest any line gets is what bounds the line count, so a positive
    first-line indent is subtracted and a negative one is not: treating the
    hanging line as narrower than it is only ever over-counts lines, which is
    the safe direction, while ignoring a first-line indent under-counts them.

    Named and shared rather than inlined, because the frame's width and the
    width a paragraph is laid out in are different quantities and this module
    read the first where it meant the second. The loader had ``marL`` all along.
    """
    width = available - (paragraph.margin_left_pt or 0.0)
    width -= max(0.0, paragraph.indent_pt or 0.0)
    return max(0.0, width)


def _paragraph_words(paragraph: TextParagraph) -> tuple[list[float], float]:
    """An upper bound on each word's advance, and on one space.

    A word is measured in the face of every run it spans, because a run
    boundary can fall inside one -- a bolded stem, an italicised suffix -- and
    splitting the word there would measure two narrow pieces instead of one
    wide one.
    """
    widths: list[float] = []
    current = 0.0
    started = False
    space = 0.0
    for run in paragraph.runs:
        size_pt = _run_size(run)
        space = max(space, size_pt * MAX_ADVANCE_EM)
        piece = ""
        for character in run.text:
            if character.isspace():
                current += _text_width(run, piece)
                piece = ""
                if started:
                    widths.append(current)
                current = 0.0
                started = False
                continue
            piece += character
            started = True
        current += _text_width(run, piece)
    if started:
        widths.append(current)
    return widths, space


def _wrapped_lines(paragraph: TextParagraph, available: float) -> int:
    """How many lines this paragraph needs, wrapping at word boundaries.

    Dividing the paragraph's total advance by the available width and rounding
    up is *not* an upper bound, because wrapping does not pack that tightly: four
    words each a little over half the line width need four lines, while the
    division says three. The module's whole guarantee is that it never narrows a
    box past its ink, and that arithmetic broke it -- by 40% on a box measured
    against this, which is enough to hide an overlap or a margin breach.

    A word wider than the line is character-wrapped by PowerPoint, so it is
    counted for every line it spans rather than for one.
    """
    available = paragraph_available_width(available, paragraph)
    if available <= 0:
        return 1
    words, space = _paragraph_words(paragraph)
    if not words:
        return 1
    lines = 0
    current = 0.0
    for width in words:
        if width > available:
            # Ends whatever line was open, then runs over as many as it needs.
            lines += (1 if current else 0) + math.ceil(width / available)
            current = 0.0
            continue
        if not current:
            lines += 1
            current = width
        elif current + space + width <= available:
            current += space + width
        else:
            lines += 1
            current = width
    return max(1, lines)


def _paragraph_width_bound(paragraph: TextParagraph) -> float:
    """An upper bound on the width one paragraph would need on a single line."""
    return sum(_run_width(run) for run in paragraph.runs)


def _paragraph_line_height_bound(paragraph: TextParagraph) -> float:
    """An upper bound on one line of this paragraph, honouring explicit spacing."""
    largest = max(
        (_run_line_height(run) for run in paragraph.runs),
        default=ASSUMED_SIZE_PT * MAX_LINE_EM,
    )
    height = largest
    # ``line_spacing`` is a multiple when it is a multiple and points when it is
    # points; both are in the model as a float, so the larger reading is taken.
    spacing = paragraph.line_spacing
    if spacing and spacing > 0:
        height = max(height, largest * spacing, float(spacing))
    return height


def _is_text_only(shape: ShapeModel) -> bool:
    """Whether text is the only thing this shape draws.

    A fill or an outline paints the whole frame, so the frame *is* the ink and
    there is nothing to narrow. Tables, charts and pictures draw their own
    content, and groups have no visual of their own.
    """
    if shape.table is not None or shape.chart is not None:
        return False
    if shape.kind in ("group", "picture", "media", "ole", "connector"):
        return False
    if not shape.text_frame_paragraphs or not shape.has_text:
        return False
    fill = shape.effective_fill
    if fill is not None and fill.kind not in ("none", "inherit"):
        return False
    line = shape.effective_line
    return not (line is not None and line.is_visible)


def ink_extent(shape: ShapeModel) -> InkExtent | None:
    """A rectangle proven to contain ``shape``'s ink, or None to use the frame.

    Returns None for anything whose frame is already the honest measurement: a
    filled or outlined shape, a picture, a table, a chart, a rotated shape (the
    rotation-aware frame is the reader's view and narrowing inside it would need
    the glyph run's own rotated hull), or a text box whose bound reaches both
    edges of its frame anyway.
    """
    if shape.rotation or shape.width_pt <= 0 or shape.height_pt <= 0:
        return None
    if not _is_text_only(shape):
        return None

    available_width = shape.width_pt - shape.inset_left_pt - shape.inset_right_pt
    available_height = shape.height_pt - shape.inset_top_pt - shape.inset_bottom_pt
    if available_width <= 0 or available_height <= 0:
        return None

    paragraphs = [p for p in shape.text_frame_paragraphs if p.text]
    if not paragraphs:
        return None

    wraps = shape.word_wrap is not False
    widest = 0.0
    total_height = 0.0
    for paragraph in paragraphs:
        bound = _paragraph_width_bound(paragraph)
        # An indented paragraph is laid out in less than the frame's width and
        # its ink starts further in. Both matter: the first decides how many
        # lines it takes, the second where its widest line ends.
        indent = available_width - paragraph_available_width(available_width, paragraph)
        if wraps:
            lines = _wrapped_lines(paragraph, available_width)
            widest = max(widest, min(bound + indent, available_width))
        else:
            # No wrapping: the text runs on as one line, which may be wider than
            # the frame. That is LO-005's finding, not a narrowing.
            lines = 1
            widest = max(widest, bound + indent)
        total_height += lines * _paragraph_line_height_bound(paragraph)
        total_height += (paragraph.space_before_pt or 0.0) + (paragraph.space_after_pt or 0.0)

    narrowed_x = widest < available_width
    narrowed_y = total_height < available_height
    if not narrowed_x and not narrowed_y:
        return None

    left, width = _place_horizontally(shape, paragraphs, widest, available_width, narrowed_x)
    top, height = _place_vertically(shape, total_height, available_height, narrowed_y)
    return InkExtent(
        left=left, top=top, width=width, height=height,
        narrowed_x=narrowed_x, narrowed_y=narrowed_y,
        measured=_all_measured(paragraphs),
    )


def _all_measured(paragraphs: list[TextParagraph]) -> bool:
    """Whether every run with text in it resolved a typeface to measure in."""
    return all(
        _run_font_path(run) is not None
        for paragraph in paragraphs
        for run in paragraph.runs
        if run.text
    )


def ink_confidence(shape: ShapeModel) -> Literal["high", "medium"]:
    """How far a geometric finding about ``shape`` can be trusted.

    A rule declares one confidence for every finding it makes. That is wrong
    for the geometric rules, whose findings inherit the layout model's error,
    and the model's error is not one number: text measured in its own typeface
    is a measurement, text bounded at 1.15 em per character is a rectangle the
    ink is somewhere inside. An overlap between two bounded boxes may be an
    overlap of two bounds and no ink at all. The same rule, the same declared
    confidence, materially different reliability -- and until now no way to
    say so.

    A shape whose frame is its ink -- a filled or outlined shape, a picture, a
    table -- is measured by definition. A text-only shape is as trustworthy as
    its weakest run.
    """
    if not _is_text_only(shape):
        return "high"
    paragraphs = [p for p in shape.text_frame_paragraphs if p.text]
    return "high" if _all_measured(paragraphs) else "medium"


def _place_horizontally(
    shape: ShapeModel,
    paragraphs: list[TextParagraph],
    widest: float,
    available: float,
    narrowed: bool,
) -> tuple[float, float]:
    """Where the ink sits across the frame, given the paragraphs' alignment."""
    box_left = shape.left_pt + shape.inset_left_pt
    if not narrowed:
        return (box_left, available)

    alignments = {p.alignment for p in paragraphs}
    # Mixed alignment, or justified text, can put ink anywhere across the frame.
    if len(alignments) > 1 or alignments & SPREAD_ALIGNMENTS:
        return (box_left, available)

    alignment = next(iter(alignments), None)
    if alignment == ALIGN_CENTRE:
        return (box_left + (available - widest) / 2.0, widest)
    if alignment == ALIGN_RIGHT:
        return (box_left + available - widest, widest)
    # Left, or inherited and therefore left in every house style TieOut has met.
    return (box_left, widest)


def _place_vertically(
    shape: ShapeModel, needed: float, available: float, narrowed: bool
) -> tuple[float, float]:
    """Where the ink sits down the frame, given the body's vertical anchor."""
    box_top = shape.top_pt + shape.inset_top_pt
    if not narrowed:
        return (box_top, available)
    anchor = (shape.vertical_anchor or "top").lower()
    if anchor in ("ctr", "center", "middle"):
        return (box_top + (available - needed) / 2.0, needed)
    if anchor in ("b", "bottom"):
        return (box_top + available - needed, needed)
    return (box_top, needed)


def ink_bbox_pt(shape: ShapeModel) -> tuple[float, float, float, float]:
    """``shape``'s ink rectangle, falling back to its rotation-aware frame."""
    extent = ink_extent(shape)
    return extent.bbox if extent is not None else shape.visual_bbox_pt


# --------------------------------------------------------------------------------------
# Deliberate bleeds
# --------------------------------------------------------------------------------------


def _overlap(low: float, high: float, limit_low: float, limit_high: float) -> float:
    return max(0.0, min(high, limit_high) - max(low, limit_low))


def is_decorative_bleed(
    shape: ShapeModel, slide: SlideModel, canvas: tuple[float, float]
) -> bool:
    """Whether ``shape`` reads as a graphic deliberately bled off the canvas.

    The signals, all structural because intent is not recorded in the file:

    * it carries no text and no content of its own, so nothing a reader must
      read is being cut off;
    * part of it is on the canvas and part of it is off, which is a bleed --
      a shape *entirely* off the canvas is abandoned work, not decoration;
    * it sits in the back half of the slide's z-order, behind the content it
      decorates rather than on top of it.

    Being wrong here costs a downgrade to ``info``, not silence, so the test is
    deliberately permissive about shape kind and generous about z-order.
    """
    if shape.has_text or shape.table is not None or shape.chart is not None:
        return False
    if shape.kind in ("group", "media", "ole"):
        return False
    width, height = canvas
    if width <= 0 or height <= 0:
        return False

    left, top, box_width, box_height = shape.visual_bbox_pt
    area = box_width * box_height
    if area <= 0:
        return False
    inside = _overlap(left, left + box_width, 0.0, width) * _overlap(
        top, top + box_height, 0.0, height
    )
    inside_share = inside / area
    if inside_share < BLEED_MIN_INSIDE_SHARE:
        return False
    if (1.0 - inside_share) < BLEED_MIN_OUTSIDE_SHARE:
        return False

    orders = [other.z_order for other in slide.leaf_shapes()]
    if not orders:
        return True
    threshold = min(orders) + BLEED_BACK_Z_SHARE * (max(orders) - min(orders))
    return shape.z_order <= threshold
