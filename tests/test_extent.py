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

from collections.abc import Sequence

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
    text: str,
    size_pt: float | None = 10.0,
    *,
    alignment: str | None = None,
    space_before_pt: float | None = None,
    space_after_pt: float | None = None,
) -> TextParagraph:
    return TextParagraph(
        runs=(TextRun(text=text, font=_font(size_pt)),),
        level=0,
        alignment=alignment,
        space_before_pt=space_before_pt,
        space_after_pt=space_after_pt,
    )


def _shape(
    *,
    left: float = 100.0,
    top: float = 100.0,
    width: float = 200.0,
    height: float = 40.0,
    paragraphs: Sequence[TextParagraph] = (),
    kind: str = "textbox",
    fill: ResolvedFill | None = None,
    line: ResolvedLine | None = None,
    rotation: float = 0.0,
    z_order: int = 0,
    shape_id: int = 1,
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
    )


def _slide(shapes: Sequence[ShapeModel]) -> SlideModel:
    return SlideModel(index=1, shapes=tuple(shapes), width_pt=960.0, height_pt=540.0)


# --------------------------------------------------------------------------------------
# The bound
# --------------------------------------------------------------------------------------


def test_short_text_in_a_wide_frame_narrows_to_the_text() -> None:
    shape = _shape(width=400.0, paragraphs=[_paragraph("Hi", 10.0)])
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.narrowed_x
    assert extent.width < 400.0


def test_the_width_bound_is_never_tighter_than_the_widest_possible_glyphs() -> None:
    """The claim the suppressions rest on, asserted as arithmetic.

    Two characters at 10pt cannot advance further than ``2 * 10 * MAX_ADVANCE_EM``
    in any proportional Latin face, so the bound must be at least that.
    """
    shape = _shape(width=400.0, paragraphs=[_paragraph("Hi", 10.0)])
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.width >= 2 * 10.0 * MAX_ADVANCE_EM - 1e-9


def test_the_height_bound_covers_a_full_line() -> None:
    shape = _shape(width=400.0, height=200.0, paragraphs=[_paragraph("Hi", 10.0)])
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.height >= 10.0 * MAX_LINE_EM - 1e-9


def test_text_that_fills_its_frame_is_not_narrowed_horizontally() -> None:
    shape = _shape(width=30.0, paragraphs=[_paragraph("A very long line indeed", 12.0)])
    extent = ink_extent(shape)
    assert extent is None or not extent.narrowed_x


def test_a_filled_shape_is_its_frame() -> None:
    """A fill paints the whole frame, so there is nothing to narrow."""
    shape = _shape(
        width=400.0,
        paragraphs=[_paragraph("Hi")],
        fill=ResolvedFill(kind="solid", hex="FF0000"),
    )
    assert ink_extent(shape) is None


def test_an_outlined_shape_is_its_frame() -> None:
    shape = _shape(
        width=400.0,
        paragraphs=[_paragraph("Hi")],
        line=ResolvedLine(kind="solid", hex="FF0000", width_pt=1.0),
    )
    assert ink_extent(shape) is None


def test_a_rotated_shape_is_its_frame() -> None:
    shape = _shape(width=400.0, rotation=45.0, paragraphs=[_paragraph("Hi")])
    assert ink_extent(shape) is None


def test_a_run_with_no_resolved_size_does_not_narrow_on_a_guess() -> None:
    """An unsized run is bounded by a size larger than any body text.

    Guessing small here would shrink the box past its ink, which is the one
    thing the bound must never do.
    """
    extent = ink_extent(_shape(width=100.0, paragraphs=[_paragraph("Hello there", None)]))
    assert extent is None or not extent.narrowed_x


@pytest.mark.parametrize(
    "alignment, expected_left",
    [("left", 100.0), ("center", None), ("right", None)],
)
def test_alignment_places_the_ink(alignment, expected_left) -> None:
    shape = _shape(width=400.0, paragraphs=[_paragraph("Hi", alignment=alignment)])
    extent = ink_extent(shape)
    assert extent is not None
    if expected_left is not None:
        assert extent.left == pytest.approx(expected_left)
    else:
        assert extent.left > 100.0


def test_mixed_alignment_keeps_the_full_frame_width() -> None:
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


def test_ink_bbox_falls_back_to_the_visual_frame() -> None:
    shape = _shape(kind="picture", paragraphs=[])
    assert ink_bbox_pt(shape) == shape.visual_bbox_pt


# --------------------------------------------------------------------------------------
# Bleeds
# --------------------------------------------------------------------------------------


def _bleed_shape(
    *,
    paragraphs: Sequence[TextParagraph] = (),
    shape_id: int = 1,
    z_order: int = 0,
) -> ShapeModel:
    return _shape(
        left=800.0,
        top=-100.0,
        width=300.0,
        height=300.0,
        kind="autoshape",
        paragraphs=paragraphs,
        shape_id=shape_id,
        z_order=z_order,
    )


def test_an_untexted_graphic_crossing_the_edge_is_a_bleed() -> None:
    shape = _bleed_shape()
    assert is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_shape_carrying_text_is_not_a_bleed() -> None:
    """Text running off the canvas is text a reader cannot read."""
    shape = _bleed_shape(paragraphs=[_paragraph("Important")])
    assert not is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_shape_wholly_off_the_canvas_is_not_a_bleed() -> None:
    """Abandoned work, not decoration: a bleed is partly visible by definition."""
    shape = _shape(left=1100.0, top=100.0, width=100.0, height=100.0, kind="autoshape")
    assert not is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_shape_wholly_on_the_canvas_is_not_a_bleed() -> None:
    shape = _shape(left=100.0, top=100.0, width=100.0, height=100.0, kind="autoshape")
    assert not is_decorative_bleed(shape, _slide([shape]), CANVAS)


def test_a_graphic_in_front_of_the_content_is_not_a_bleed() -> None:
    """A bleed decorates from behind; something on top is in the way."""
    back = _shape(shape_id=1, z_order=0, kind="autoshape")
    front = _bleed_shape(shape_id=2, z_order=99)
    slide = _slide([back, front])
    assert not is_decorative_bleed(front, slide, CANVAS)


# --------------------------------------------------------------------------------------
# Data series: a shape whose position carries a number
# --------------------------------------------------------------------------------------


def _at(left: float, top: float, width: float, height: float, shape_id: int) -> ShapeModel:
    return _shape(
        left=left, top=top, width=width, height=height, kind="autoshape", shape_id=shape_id
    )


def test_a_scatter_of_same_sized_shapes_is_a_data_series() -> None:
    """Five dots on a quadrant. Where each sits is the reading."""
    from tieout.rules.layout import _data_series_axes

    dots = [
        _at(286.85, 235.73, 11.52, 11.52, 1),
        _at(263.16, 313.56, 11.52, 11.52, 2),
        _at(212.40, 222.19, 11.52, 11.52, 3),
        _at(307.15, 347.40, 11.52, 11.52, 4),
        _at(137.95, 415.08, 11.52, 11.52, 5),
    ]
    plotted = _data_series_axes(dots, 2.0)
    for dot in dots:
        assert plotted.get(dot.ref.shape_id) == frozenset({"x", "y"})


def test_a_row_of_cards_nudged_off_one_column_is_not_a_data_series() -> None:
    """The protection that keeps a real defect reportable.

    Five same-sized cards 3pt off a column share their left edge with each
    other. Every one is removed as laid out, and nothing remains to call a
    series -- so LO-003 still has them to report.
    """
    from tieout.rules.layout import _data_series_axes

    cards = [_at(57.6, 100.0 + index * 70.0, 200.0, 60.0, index) for index in range(5)]
    plotted = _data_series_axes(cards, 2.0)
    assert all("y" not in plotted.get(card.ref.shape_id, frozenset()) for card in cards)


def test_a_group_holding_both_a_column_and_a_scatter_is_partitioned() -> None:
    """A football field: three method names down a left column, three bars
    running to wherever their value ends, all the same height.

    Filtering the group on "unaligned" would throw all six away on account of
    the three; partitioning keeps the column reportable and the bars exempt.
    """
    from tieout.rules.layout import _data_series_axes

    column = [_at(39.6, 154.8 + index * 67.68, 198.0, 37.44, index) for index in range(3)]
    bars = [
        _at(678.56, 154.80, 113.52, 37.44, 10),
        _at(719.84, 222.48, 113.52, 37.44, 11),
        _at(702.64, 290.16, 110.08, 37.44, 12),
    ]
    plotted = _data_series_axes([*column, *bars], 2.0)
    assert all("x" not in plotted.get(entry.ref.shape_id, frozenset()) for entry in column)
    assert all("x" in plotted.get(bar.ref.shape_id, frozenset()) for bar in bars)


def test_two_scattered_shapes_are_not_a_series() -> None:
    """Two shapes that align to nothing are two loose shapes."""
    from tieout.rules.layout import _data_series_axes

    pair = [_at(100.0, 100.0, 20.0, 20.0, 1), _at(240.0, 333.0, 20.0, 20.0, 2)]
    assert _data_series_axes(pair, 2.0) == {}


def test_a_row_of_equal_cards_is_not_a_data_series() -> None:
    """The other half of the protection: equal cards across, not down.

    Their lefts differ by construction, so the shared-position partition keeps
    them all; what disqualifies them is that their widths do not vary and their
    tops are shared. A bar chart varies in length; a row of cards does not.
    """
    from tieout.rules.layout import _data_series_axes

    cards = [_at(36.0 + index * 300.0, 120.0, 280.0, 106.0, index) for index in range(3)]
    plotted = _data_series_axes(cards, 2.0)
    assert all("x" not in plotted.get(card.ref.shape_id, frozenset()) for card in cards)


# --------------------------------------------------------------------------------------
# Measured where the face is available, bounded where it is not
# --------------------------------------------------------------------------------------


def _arial_run(text: str, size_pt: float = 10.0) -> TextParagraph:
    """Arial, for which Liberation Sans is a metric-compatible substitute.

    Installed on this machine and on CI, which is why the measured path can be
    asserted at all; Calibri and Cambria cannot be shipped to a build machine.
    """
    font = ResolvedFont(
        name="Arial",
        size_pt=size_pt,
        bold=False,
        italic=False,
        underline=False,
        color_hex="000000",
    )
    return TextParagraph(runs=(TextRun(text=text, font=font),), level=0)


def test_an_available_face_is_measured_rather_than_bounded() -> None:
    """The measurement is tighter than the bound, and still contains the ink."""
    from tieout.model.fonts import resolve_font_path

    if resolve_font_path("Arial", bold=False, italic=False) is None:
        pytest.skip("no metric-compatible substitute for Arial is installed")

    text = "Measured rather than bounded"
    shape = _shape(width=600.0, paragraphs=[_arial_run(text)])
    extent = ink_extent(shape)
    assert extent is not None
    assert extent.narrowed_x

    bound = len(text) * 10.0 * MAX_ADVANCE_EM
    assert extent.width < bound, "the measurement should be tighter than the bound"
    assert extent.width > 0


def test_an_unavailable_face_falls_back_to_the_bound() -> None:
    """A face nothing stands in for is bounded, never substituted.

    A measurement taken in the wrong typeface is worse than no measurement,
    because it looks like a measurement.
    """
    from tieout.model.fonts import resolve_font_path

    invented = "Halyard Display Grotesk"
    assert resolve_font_path(invented, bold=False, italic=False) is None

    font = ResolvedFont(
        name=invented,
        size_pt=10.0,
        bold=False,
        italic=False,
        underline=False,
        color_hex="000000",
    )
    paragraph = TextParagraph(runs=(TextRun(text="Hi", font=font),), level=0)
    extent = ink_extent(_shape(width=400.0, paragraphs=[paragraph]))
    assert extent is not None
    assert extent.width == pytest.approx(2 * 10.0 * MAX_ADVANCE_EM)
