"""The ink bound, the bleed test, and the rules that now measure with them.

Two claims are load-bearing and are asserted directly rather than through a
rule's output.

**The ink bound is an upper bound.** It may fail to narrow a frame; it must
never narrow one past the glyphs, because everything downstream suppresses a
finding on the strength of it. A bound that is too tight turns a real defect
into silence, which is the one failure mode a QA tool cannot have.

**A bleed is not an accident.** The test is structural -- no text, part on the
canvas and part off, behind the content -- so each signal is checked by
removing it and watching the answer change.
"""

from __future__ import annotations

import pytest

from tieout.model.deck import ShapeModel, ShapeRef, SlideModel, TextParagraph, TextRun
from tieout.model.extent import (
    MAX_ADVANCE_EM,
    MAX_LINE_EM,
    ink_bbox_pt,
    ink_extent,
    is_decorative_bleed,
)
from tieout.model.inherit import ResolvedFill, ResolvedFont, ResolvedLine

CANVAS = (960.0, 540.0)


def _font(size_pt: float | None = 10.0) -> ResolvedFont:
    return ResolvedFont(
        name="Calibri",
        size_pt=size_pt,
        bold=False,
        italic=False,
        underline=False,
        color_hex="000000",
    )


def _paragraph(
    text: str, size_pt: float | None = 10.0, **kwargs: object
) -> TextParagraph:
    return TextParagraph(runs=(TextRun(text=text, font=_font(size_pt)),), level=0, **kwargs)


def _shape(
    *,
    left=100.0,
    top=100.0,
    width=200.0,
    height=40.0,
    paragraphs=(),
    kind="textbox",
    fill=None,
    line=None,
    rotation=0.0,
    z_order=0,
    shape_id=1,
    **kwargs: object,
) -> ShapeModel:
    return ShapeModel(
        ref=ShapeRef(slide_index=1, shape_id=shape_id, name=f"Shape {shape_id}"),
        kind=kind,
        left_pt=left,
        top_pt=top,
        width_pt=width,
        height_pt=height,
        rotation=rotation,
        effective_fill=fill,
        effective_line=line,
        text_frame_paragraphs=tuple(paragraphs),
        has_text_frame=bool(paragraphs),
        inset_left_pt=0.0,
        inset_right_pt=0.0,
        inset_top_pt=0.0,
        inset_bottom_pt=0.0,
        z_order=z_order,
        **kwargs,
    )


def _slide(shapes) -> SlideModel:
    return SlideModel(index=1, shapes=tuple(shapes), width_pt=960.0, height_pt=540.0)


# --------------------------------------------------------------------------------------
# The bound
# --------------------------------------------------------------------------------------


def test_short_text_in_a_wide_frame_narrows_to_the_text():
    shape = _shape(width=400.0, paragraphs=[_paragraph("Hi", 10.0)])
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.narrowed_x
    assert extent.width < 400.0


def test_the_width_bound_is_never_tighter_than_the_widest_possible_glyphs():
    """The claim the suppressions rest on, asserted as arithmetic.

    Two characters at 10pt cannot advance further than ``2 * 10 * MAX_ADVANCE_EM``
    in any proportional Latin face, so the bound must be at least that.
    """
    shape = _shape(width=400.0, paragraphs=[_paragraph("Hi", 10.0)])
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.width >= 2 * 10.0 * MAX_ADVANCE_EM - 1e-9


def test_the_height_bound_covers_a_full_line():
    shape = _shape(width=400.0, height=200.0, paragraphs=[_paragraph("Hi", 10.0)])
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.height >= 10.0 * MAX_LINE_EM - 1e-9


def test_text_that_fills_its_frame_is_not_narrowed_horizontally():
    shape = _shape(width=30.0, paragraphs=[_paragraph("A very long line indeed", 12.0)])
    extent = ink_extent(shape)
    assert extent is None or not extent.narrowed_x


def test_a_filled_shape_is_its_frame():
    """A fill paints the whole frame, so there is nothing to narrow."""
    shape = _shape(
        width=400.0,
        paragraphs=[_paragraph("Hi")],
        fill=ResolvedFill(kind="solid", hex="FF0000"),
    )
    assert ink_extent(shape) is None


def test_an_outlined_shape_is_its_frame():
    shape = _shape(
        width=400.0,
        paragraphs=[_paragraph("Hi")],
        line=ResolvedLine(kind="solid", hex="FF0000", width_pt=1.0),
    )
    assert ink_extent(shape) is None


def test_a_rotated_shape_is_its_frame():
    shape = _shape(width=400.0, rotation=45.0, paragraphs=[_paragraph("Hi")])
    assert ink_extent(shape) is None


def test_a_run_with_no_resolved_size_does_not_narrow_on_a_guess():
    """An unsized run is bounded by a size larger than any body text.

    Guessing small here would shrink the box past its ink, which is the one
    thing the bound must never do.
    """
    wide = _shape(width=100.0, paragraphs=[_paragraph("Hello there", None)])
    assert ink_extent(wide) is None or not ink_extent(wide).narrowed_x


@pytest.mark.parametrize(
    "alignment, expected_left",
    [("left", 100.0), ("center", None), ("right", None)],
)
def test_alignment_places_the_ink(alignment, expected_left):
    shape = _shape(width=400.0, paragraphs=[_paragraph("Hi", alignment=alignment)])
    extent = ink_extent(shape)
    assert extent is not None
    if expected_left is not None:
        assert extent.left == pytest.approx(expected_left)
    else:
        assert extent.left > 100.0


def test_mixed_alignment_keeps_the_full_frame_width():
    """Nothing says where ink sits when paragraphs disagree, so nothing is claimed."""
    shape = _shape(
        width=400.0,
        paragraphs=[
            _paragraph("Hi", alignment="left"),
            _paragraph("There", alignment="right"),
        ],
    )
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.left == pytest.approx(100.0)
    assert extent.width == pytest.approx(400.0)


def test_ink_bbox_falls_back_to_the_visual_frame():
    shape = _shape(kind="picture", paragraphs=[])
    assert ink_bbox_pt(shape) == shape.visual_bbox_pt


# --------------------------------------------------------------------------------------
# Bleeds
# --------------------------------------------------------------------------------------


def _bleed_shape(**kwargs: object) -> ShapeModel:
    return _shape(
        left=800.0, top=-100.0, width=300.0, height=300.0, kind="autoshape", **kwargs
    )


def test_an_untexted_graphic_crossing_the_edge_is_a_bleed():
    shape = _bleed_shape()
    assert is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_shape_carrying_text_is_not_a_bleed():
    """Text running off the canvas is text a reader cannot read."""
    shape = _bleed_shape(paragraphs=[_paragraph("Important")])
    assert not is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_shape_wholly_off_the_canvas_is_not_a_bleed():
    """Abandoned work, not decoration: a bleed is partly visible by definition."""
    shape = _shape(left=1100.0, top=100.0, width=100.0, height=100.0, kind="autoshape")
    assert not is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_shape_wholly_on_the_canvas_is_not_a_bleed():
    shape = _shape(left=100.0, top=100.0, width=100.0, height=100.0, kind="autoshape")
    assert not is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_graphic_in_front_of_the_content_is_not_a_bleed():
    """A bleed decorates from behind; something on top is in the way."""
    back = _shape(shape_id=1, z_order=0, kind="autoshape")
    front = _bleed_shape(shape_id=2, z_order=99)
    slide = _slide([back, front])
    assert not is_decorative_bleed(front, slide, CANVAS)
