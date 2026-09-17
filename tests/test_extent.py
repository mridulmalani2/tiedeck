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

import math
import random
from collections.abc import Sequence

import pytest
from pptx import Presentation
from pptx.util import Emu, Pt

from tieout.model.deck import (
    ALIGN_CENTRE,
    ShapeModel,
    ShapeRef,
    SlideModel,
    TextParagraph,
    TextRun,
)
from tieout.model.extent import (
    MAX_ADVANCE_EM,
    MAX_LINE_EM,
    ink_bbox_pt,
    ink_extent,
    is_decorative_bleed,
    paragraph_available_width,
)
from tieout.model.inherit import ResolvedFill, ResolvedFont, ResolvedLine
from tieout.model.loader import load_deck

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
    [("left", 100.0), (ALIGN_CENTRE, None), ("right", None)],
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


# --------------------------------------------------------------------------------------
# The bound has to be a bound
#
# This module's whole promise is that it never narrows a box past its ink, so a
# geometric rule reading the narrowed box cannot miss a defect the frame would
# have caught. That promise was stated in the docstring and in the README and was
# not true: line count came from dividing the paragraph's total advance by the
# available width and rounding up, and wrapping does not pack that tightly.
#
# Four words each a little over half the line width need four lines; the division
# says three. The box came out 40% shorter than its own bound allowed.
#
# These tests generate the cases rather than naming them, because naming them is
# what the suite was already doing and the arithmetic slipped through anyway.
# --------------------------------------------------------------------------------------

#: A face that is certainly not installed, so every run takes the bounded path.
#: The measured path is a real measurement and needs no bound to hold it up.
_UNINSTALLED = "NoSuchFaceEverInstalled"
_SIZE_PT = 10.0


def _lines_needed(text: str, available_pt: float) -> int:
    """Greedy word wrap under the same per-character bound the module uses."""
    per_char = _SIZE_PT * MAX_ADVANCE_EM
    lines = 0
    current = 0.0
    for word in text.split():
        width = len(word) * per_char
        if width > available_pt:
            lines += (1 if current else 0) + math.ceil(width / available_pt)
            current = 0.0
        elif not current:
            lines += 1
            current = width
        elif current + per_char + width <= available_pt:
            current += per_char + width
        else:
            lines += 1
            current = width
    return max(1, lines)


def _wrapping_deck(path, cases):
    """One slide per case. A case is ``(text, available_chars)``, optionally with
    a ``marL`` and an ``indent`` in points."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    for case in cases:
        text, available_chars = case[0], case[1]
        margin_left, indent = (*case, 0.0, 0.0)[2:4]
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        width = available_chars * _SIZE_PT * MAX_ADVANCE_EM + 14.4
        box = slide.shapes.add_textbox(Pt(20), Pt(20), Pt(width), Pt(500))
        frame = box.text_frame
        frame.word_wrap = True
        frame.text = text
        properties = frame.paragraphs[0]._p.get_or_add_pPr()
        if margin_left:
            properties.set("marL", str(int(margin_left * 12700)))
        if indent:
            properties.set("indent", str(int(indent * 12700)))
        for run in frame.paragraphs[0].runs:
            run.font.size = Pt(_SIZE_PT)
            run.font.name = _UNINSTALLED
    presentation.save(str(path))
    return load_deck(path)


def _cases():
    """The counterexample that started this, then a generated spread.

    The spread carries bullet indents too. ``marL`` and ``indent`` narrow the
    width a paragraph is laid out in, and reading the frame's width where the
    paragraph's was meant re-opened exactly the hole the wrap count closed --
    a one-inch indent put the box a third short.
    """
    cases = [
        ("aaaaa bbbbb ccccc ddddd eeeee", 10, 0.0, 0.0),
        ("aaaaa bbbbb ccccc ddddd eeeee fffff", 20, 72.0, 0.0),
    ]
    rng = random.Random(7)
    for _ in range(40):
        words = rng.randint(2, 8)
        length = rng.randint(2, 14)
        cases.append(
            (
                " ".join(chr(97 + index % 26) * length for index in range(words)),
                rng.randint(6, 40),
                rng.choice([0.0, 18.0, 36.0, 72.0]),
                rng.choice([0.0, -18.0, 18.0]),
            )
        )
    return cases


def test_the_ink_box_is_never_shorter_than_the_text_needs(tmp_path):
    cases = _cases()
    deck = _wrapping_deck(tmp_path / "wrapping.pptx", cases)

    violations = []
    for slide, case in zip(deck.slides, cases, strict=True):
        text = case[0]
        shape = next(iter(slide.leaf_shapes()))
        extent = ink_extent(shape)
        if extent is None:
            continue
        frame = shape.width_pt - shape.inset_left_pt - shape.inset_right_pt
        available = paragraph_available_width(
            frame, shape.text_frame_paragraphs[0]
        )
        needed = _lines_needed(text, available) * _SIZE_PT * MAX_LINE_EM
        if extent.height + 1e-6 < needed:
            violations.append(
                f"{text[:30]!r} in {available:.0f}pt: box {extent.height:.1f}pt, "
                f"text needs {needed:.1f}pt"
            )

    assert not violations, "the bound is not a bound:\n  " + "\n  ".join(violations)


def test_the_case_that_broke_it(tmp_path):
    """Five words at half the line width each. ceil() said three lines; they
    need five, and the box came out 45pt where the text needs 75."""
    text = "aaaaa bbbbb ccccc ddddd eeeee"
    deck = _wrapping_deck(tmp_path / "counterexample.pptx", [(text, 10, 0.0, 0.0)])
    shape = next(iter(deck.slides[0].leaf_shapes()))
    extent = ink_extent(shape)

    assert extent is not None
    available = shape.width_pt - shape.inset_left_pt - shape.inset_right_pt
    assert _lines_needed(text, available) == 5
    assert extent.height >= 5 * _SIZE_PT * MAX_LINE_EM - 1e-6


def test_a_word_wider_than_its_line_is_counted_for_every_line_it_spans(tmp_path):
    """PowerPoint character-wraps a word that cannot fit. Counting it as one
    line would narrow the box past the ink again."""
    text = "a" * 40
    deck = _wrapping_deck(tmp_path / "longword.pptx", [(text, 10, 0.0, 0.0)])
    shape = next(iter(deck.slides[0].leaf_shapes()))
    extent = ink_extent(shape)

    if extent is not None:
        available = shape.width_pt - shape.inset_left_pt - shape.inset_right_pt
        needed = _lines_needed(text, available) * _SIZE_PT * MAX_LINE_EM
        assert extent.height + 1e-6 >= needed


# --------------------------------------------------------------------------------------
# The alignment vocabulary is closed
#
# The loader spelled "centre" and this module checked for ALIGN_CENTRE, so no
# paragraph ever matched and every centred one was placed as though left aligned.
# A section divider's title was measured at the left margin while it renders in
# the middle of the slide. Found by rendering the deck and asking where the words
# actually landed; nothing in the suite could have, because the fixture's titles
# are left aligned too.
# --------------------------------------------------------------------------------------


def test_every_alignment_the_loader_emits_is_one_this_module_places():
    from tieout.model.deck import ALIGN_RIGHT, ALIGNMENTS, SPREAD_ALIGNMENTS
    from tieout.model.loader import _ALIGNMENT_MAP

    emitted = set(_ALIGNMENT_MAP.values())
    assert emitted <= ALIGNMENTS, f"the loader emits {emitted - ALIGNMENTS} nobody names"
    # Every value is either one of the two the placer moves ink for, one it
    # declines to narrow, or the left default it falls through to.
    placed = {ALIGN_CENTRE, ALIGN_RIGHT} | SPREAD_ALIGNMENTS | {"left"}
    assert emitted <= placed, f"{emitted - placed} would silently fall through to left"


def test_a_centred_paragraph_is_placed_in_the_middle_of_its_frame():
    shape = _shape(
        left=36.0,
        top=300.0,
        width=888.0,
        height=30.0,
        paragraphs=[_paragraph("Section I", 24.0, alignment=ALIGN_CENTRE)],
    )
    extent = ink_extent(shape)
    assert extent is not None and extent.narrowed_x
    frame_centre = 36.0 + 888.0 / 2.0
    ink_centre = extent.left + extent.width / 2.0
    assert abs(ink_centre - frame_centre) < 1e-6, (
        f"ink centred at {ink_centre:.1f}, frame at {frame_centre:.1f}"
    )
    assert extent.left > 36.0 + 200.0, "a centred title does not start at the left margin"



# --------------------------------------------------------------------------------------
# The model says how sure it is
# --------------------------------------------------------------------------------------


def _shape_in_face(face: str, fill: ResolvedFill | None = None) -> ShapeModel:
    font = ResolvedFont(
        name=face, size_pt=10.0, bold=False, italic=False, underline=False, color_hex="000000"
    )
    paragraph = TextParagraph(runs=(TextRun(text="Revenue grew", font=font),), level=0)
    return _shape(width=400.0, paragraphs=[paragraph], fill=fill)


def test_a_bounded_run_makes_the_extent_and_the_confidence_medium():
    """Text bounded at 1.15em per character is a rectangle the ink is somewhere
    inside, not a measurement, and a finding built on it must say so."""
    from tieout.model.extent import ink_confidence

    shape = _shape_in_face("NoSuchFaceEverInstalled")
    extent = ink_extent(shape)
    assert extent is not None and not extent.measured
    assert ink_confidence(shape) == "medium"


def test_a_measured_run_keeps_high_confidence():
    """Liberation Sans is installed wherever the suite runs, including CI."""
    from tieout.model.extent import ink_confidence
    from tieout.model.fonts import resolve_font_path

    if resolve_font_path("Liberation Sans", bold=False, italic=False) is None:
        pytest.skip("Liberation Sans is not installed here")
    shape = _shape_in_face("Liberation Sans")
    extent = ink_extent(shape)
    assert extent is not None and extent.measured
    assert ink_confidence(shape) == "high"


def test_a_filled_shape_is_measured_by_definition():
    """A fill paints the whole frame, so the frame is the ink and there is no
    bound involved, whatever typeface the text is in."""
    from tieout.model.extent import ink_confidence

    shape = _shape_in_face(
        "NoSuchFaceEverInstalled",
        fill=ResolvedFill(kind="solid", hex="112233", source="test"),
    )
    assert ink_confidence(shape) == "high"
