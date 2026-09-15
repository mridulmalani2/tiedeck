"""Where a shape's ink actually lands, as distinct from where its box sits.

PowerPoint stores a text box's *frame*, not its glyphs. A 6pt wordmark left
aligned in a 165pt box occupies about a third of it, and the rest is empty. Any
geometric rule that measures the frame therefore reports overflow, overlap and
gutters that no reader can see -- which is the single largest source of false
positives on a real deck, where generously sized text boxes are the norm.

This module narrows a text shape's box to a rectangle that **provably contains**
its ink, and never to anything smaller. Every estimate here is an upper bound:

* :data:`MAX_ADVANCE_EM` bounds the width of one character. No widely used
  proportional Latin face advances more than about 0.95 em on its widest glyph,
  and PowerPoint's letter spacing (``spc``) adds at most a fifth of an em in
  practice, so 1.15 em per character cannot be exceeded by real text.
* :data:`MAX_LINE_EM` bounds one line's height against the largest run on it.

Bounds rather than metrics because the font files are usually absent. TieOut
runs on servers that have never had Calibri installed, and LO-006 already ships
disabled for exactly that reason. A bound needs no font file, and being an upper
bound it can only ever *fail to narrow* -- it cannot shrink a box past its ink
and hide a real defect. Where the narrowed box still reaches the edge of the
frame on an axis, that axis is left at the frame, and the caller measures what
the file says.

The second thing this module names is a **deliberate bleed**: an untexted shape
crossing a slide edge, which is how a cover graphic is drawn and not how a
mistake looks. Nothing in the file records the designer's intent, so the test is
structural -- no text, no content, part in and part out, sitting behind the
content it decorates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from tieout.model.deck import ShapeModel, SlideModel, TextParagraph

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

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.left, self.top, self.width, self.height)

    @property
    def narrowed(self) -> bool:
        return self.narrowed_x or self.narrowed_y


def _run_size(run) -> float:
    size = run.font.size_pt if run.font is not None else None
    return float(size) if size and size > 0 else ASSUMED_SIZE_PT


def _paragraph_width_bound(paragraph: TextParagraph) -> float:
    """An upper bound on the width one paragraph would need on a single line."""
    return sum(len(run.text) * _run_size(run) * MAX_ADVANCE_EM for run in paragraph.runs)


def _paragraph_line_height_bound(paragraph: TextParagraph) -> float:
    """An upper bound on one line of this paragraph, honouring explicit spacing."""
    largest = max((_run_size(run) for run in paragraph.runs), default=ASSUMED_SIZE_PT)
    height = largest * MAX_LINE_EM
    # ``line_spacing`` is a multiple when it is a multiple and points when it is
    # points; both are in the model as a float, so the larger reading is taken.
    spacing = paragraph.line_spacing
    if spacing and spacing > 0:
        height = max(height, largest * MAX_LINE_EM * spacing, float(spacing))
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
        if wraps:
            lines = max(1, math.ceil(bound / available_width)) if available_width else 1
            widest = max(widest, min(bound, available_width))
        else:
            # No wrapping: the text runs on as one line, which may be wider than
            # the frame. That is LO-005's finding, not a narrowing.
            lines = 1
            widest = max(widest, bound)
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
    )


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
    if len(alignments) > 1 or alignments & {"justify", "justify_low", "distribute"}:
        return (box_left, available)

    alignment = next(iter(alignments), None)
    if alignment in ("center", "ctr"):
        return (box_left + (available - widest) / 2.0, widest)
    if alignment in ("right", "r"):
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
