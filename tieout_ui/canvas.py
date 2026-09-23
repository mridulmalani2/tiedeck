"""Serialising a slide as a shape tree, for a live surface rather than a photo.

PLAN.md 5.1: the editor draws the model instead of photographing it with
LibreOffice, so a correction has somewhere to show up other than a raster that
was made before it happened. Every value here already exists on
:class:`~tieout.model.deck.ShapeModel` -- resolved fill, resolved font, slide-
space geometry with group transforms applied -- so this module's whole job is
arranging it for the browser, not deriving anything new.

Kept apart from the server for the same reason :mod:`tieout_ui.view` is: a
slide is a pure function of the model, testable without an HTTP client, and a
canvas that quietly computed its own answer to "where is this shape" would be
a second geometry engine free to disagree with the rules that audit it.

**What this does not do.** It does not lay out a table, draw a chart series or
read a diagram part -- TieOut does not model any of those closely enough to
redraw them, and drawing something plausible in their place would be exactly
the "confident nonsense" the model layer already refuses. Where the surface
cannot draw a shape faithfully, it says so and gives the shape's true
position and size, so the placeholder is at least positioned honestly.
"""

from __future__ import annotations

from typing import Any, Final

from tieout.model.deck import DeckModel, ShapeModel, SlideModel, TextParagraph, TextRun
from tieout.model.inherit import ResolvedFill, ResolvedFont, ResolvedLine
from tieout.profile.schema import Profile
from tieout.rules.layout import data_mark_uids
from tieout_ui.view import movable

__all__ = ["canvas_view"]

#: Shape kinds the surface draws as a labelled placeholder at the shape's true
#: geometry, rather than attempting the content. Each names the part of the
#: model that stops here -- see the module docstring.
_PLACEHOLDER_REASONS: Final[dict[str, str]] = {
    "chart": (
        "TieOut does not model chart layout, so it cannot redraw this chart "
        "faithfully. Shown at its true position and size."
    ),
    "smartart": (
        "This shape's geometry lives in a diagram part TieOut does not read. "
        "Shown at its true position and size, never a guess."
    ),
    "ole": (
        "An embedded object. TieOut can move and resize it but not draw or "
        "edit its content."
    ),
    "media": (
        "Embedded video or audio. TieOut can move and resize it but not draw "
        "or edit its content."
    ),
    "unknown": "TieOut could not identify this shape's kind.",
}

#: Kinds whose own text frame the editor may change in place.
#:
#: A table is not here and does not need to be: its cells are editable through
#: ``cell_text_editable`` below, which is a different address and therefore a
#: different flag. A chart is not here either, and that one is a real refusal --
#: a series' values live in a cached copy and in an embedded workbook, and
#: TieOut writes neither.
_TEXT_EDITABLE_KINDS: Final[frozenset[str]] = frozenset(
    {"autoshape", "textbox", "connector", "freeform", "placeholder"}
)


def canvas_view(slide: SlideModel, deck: DeckModel, profile: Profile) -> dict[str, Any]:
    """The slide, as the live surface draws it.

    Flat rather than nested: every geometric value on :class:`ShapeModel` is
    already in true slide space with group transforms applied, and a group
    itself carries no visual of its own (:meth:`SlideModel.leaf_shapes`
    excludes it for the same reason). So the surface needs no group-transform
    math of its own to *draw* a slide -- only to *write back* a move or a
    resize inside one, which is a fact about the correction path, not about
    rendering, and belongs to :mod:`tieout_fix`.
    """
    marks = data_mark_uids(slide, deck, profile)
    shapes = sorted(slide.leaf_shapes(), key=lambda shape: shape.z_order)
    return {
        "width_pt": slide.width_pt,
        "height_pt": slide.height_pt,
        "background": _fill(slide.background),
        "shapes": [_shape(shape, shape.ref.uid in marks) for shape in shapes],
    }


def _shape(shape: ShapeModel, is_data_mark: bool) -> dict[str, Any]:
    refusal = movable(shape)
    node: dict[str, Any] = {
        "uid": shape.ref.uid,
        "shape_id": shape.ref.shape_id,
        "name": shape.ref.display_name,
        "kind": shape.kind,
        "group_path": list(shape.ref.group_path),
        "z_order": shape.z_order,
        "left_pt": shape.left_pt,
        "top_pt": shape.top_pt,
        "width_pt": shape.width_pt,
        "height_pt": shape.height_pt,
        "rotation": shape.rotation,
        "flip_h": shape.flip_horizontal,
        "flip_v": shape.flip_vertical,
        "fill": _fill(shape.effective_fill),
        "line": _line(shape.effective_line),
        "is_placeholder": shape.is_placeholder,
        "text": _text(shape),
        "table": _table(shape),
        "placeholder_content": _placeholder_content(shape),
        "image": _image(shape),
        "editable": {
            # Hard and soft refusal are kept apart on purpose. A grouped
            # shape's offset is stored in a coordinate space TieOut cannot
            # yet write into -- that is a fact about the write path, true or
            # false, never a judgement call. A data mark is a heuristic:
            # sound most of the time, provably capable of missing a chart's
            # own title bar sharing a width with two other things by
            # accident, and never sound enough to make unappealable. So a
            # grouped shape is refused outright and a data mark is instead a
            # warning the person can see and still choose to move past --
            # "not silently draggable" is answered by the confirmation, not
            # by a block nothing can lift.
            "movable": not refusal,
            "move_refused": refusal,
            "resizable": not refusal,
            "resize_refused": refusal,
            "data_mark": is_data_mark,
            "data_mark_warning": _DATA_MARK_WARNING if is_data_mark else "",
            "text_editable": (
                shape.has_text_frame and shape.kind in _TEXT_EDITABLE_KINDS
            ),
            # A table's cells, which is where a tie-out finding almost always
            # lands. This read "no cell-level write-back this tool does not
            # have" until ``tieout_fix.recell_fix`` gave it one, and the
            # consequence was sharp: the derived checks could compute a
            # correction for a margin and there was nowhere on the surface to
            # apply it. The chart half of that refusal stands.
            "cell_text_editable": shape.table is not None,
        },
    }
    return node


#: Shown before a move or a resize on a shape :func:`data_mark_uids` flagged,
#: so accepting the confirmation is an informed choice rather than a person
#: discovering afterwards that a bar's length just restated a figure.
_DATA_MARK_WARNING: Final[str] = (
    "This shape's position may state a value -- a bar's length, a point on a "
    "quadrant -- rather than just sit there. Moving or resizing it could "
    "restate a figure instead of correcting a mistake. Change the underlying "
    "number instead if that's what this is; continue only if it isn't."
)


def _fill(fill: ResolvedFill | None) -> dict[str, Any] | None:
    if fill is None:
        return None
    return {"kind": fill.kind, "hex": fill.hex, "alpha": fill.alpha}


def _line(line: ResolvedLine | None) -> dict[str, Any] | None:
    if line is None:
        return None
    return {
        "kind": line.kind,
        "hex": line.hex,
        "alpha": line.alpha,
        "width_pt": line.width_pt,
    }


def _font(font: ResolvedFont | None) -> dict[str, Any] | None:
    if font is None:
        return None
    return {
        "name": font.name,
        "size_pt": font.size_pt,
        "bold": font.bold,
        "italic": font.italic,
        "underline": font.underline,
        "color_hex": font.color_hex,
    }


def _run(run: TextRun, paragraph_index: int, run_index: int) -> dict[str, Any]:
    return {
        # The address a text edit is written back to -- see
        # tieout_fix.retext_fix. Not the run's identity across a re-audit,
        # which finding_key already provides some other way; this is purely
        # "the third run of the second paragraph", which is what the XML
        # writer needs and all it needs.
        "paragraph": paragraph_index,
        "run": run_index,
        "text": run.text,
        "font": _font(run.font),
        "hyperlink": run.hyperlink,
    }


def _paragraph(paragraph: TextParagraph, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "alignment": paragraph.alignment,
        "level": paragraph.level,
        "is_bulleted": paragraph.is_bulleted,
        "bullet_char": paragraph.bullet_char,
        "line_spacing": paragraph.line_spacing,
        "space_before_pt": paragraph.space_before_pt,
        "space_after_pt": paragraph.space_after_pt,
        "margin_left_pt": paragraph.margin_left_pt,
        "indent_pt": paragraph.indent_pt,
        "runs": [
            _run(run, index, run_index)
            for run_index, run in enumerate(paragraph.runs)
        ],
    }


def _text(shape: ShapeModel) -> dict[str, Any] | None:
    if not shape.has_text_frame:
        return None
    return {
        "paragraphs": [
            _paragraph(paragraph, index)
            for index, paragraph in enumerate(shape.text_frame_paragraphs)
        ],
        "word_wrap": shape.word_wrap,
        "autofit": shape.autofit,
        "vertical_anchor": shape.vertical_anchor,
        "text_direction": shape.text_direction,
        "columns": shape.text_columns,
        "column_spacing_pt": shape.column_spacing_pt,
        "inset_pt": {
            "left": shape.inset_left_pt,
            "right": shape.inset_right_pt,
            "top": shape.inset_top_pt,
            "bottom": shape.inset_bottom_pt,
        },
        # A shape can carry both a text frame and a diagram or chart -- rare,
        # but the fallback text some authoring tools leave in a graphicFrame's
        # sibling shape is one -- so the default font is offered even where no
        # run states one of its own.
        "default_font": _font(shape.effective_font),
    }


def _table(shape: ShapeModel) -> dict[str, Any] | None:
    table = shape.table
    if table is None:
        return None
    return {
        "row_count": table.row_count,
        "column_count": table.column_count,
        "column_widths_pt": list(table.column_widths_pt),
        "row_heights_pt": list(table.row_heights_pt),
        "first_row_is_header": table.first_row_is_header,
        "first_column_is_header": table.first_column_is_header,
        "banded_rows": table.banded_rows,
        "cells": [
            {
                "row": cell.row,
                "column": cell.column,
                "row_span": cell.row_span,
                "column_span": cell.column_span,
                "is_merge_continuation": cell.is_merge_continuation,
                "fill": _fill(cell.fill),
                "paragraphs": [
                    _paragraph(paragraph, index)
                    for index, paragraph in enumerate(cell.paragraphs)
                ],
            }
            for cell in table.cells
        ],
    }


def _placeholder_content(shape: ShapeModel) -> dict[str, Any] | None:
    """Why the surface cannot draw this shape's content, for the kinds it never
    can -- chart, SmartArt, OLE and media. Tables are drawn for real, and a
    picture is drawn from its own bytes rather than placeheld."""
    reason = _PLACEHOLDER_REASONS.get(shape.kind)
    if reason is None:
        return None
    node: dict[str, Any] = {"reason": reason}
    if shape.kind == "chart" and shape.chart is not None:
        node["chart_type"] = shape.chart.chart_type
        node["title"] = shape.chart.title_text
    if shape.kind == "smartart" and shape.diagram_text:
        node["labels"] = list(shape.diagram_text)
    return node


def _image(shape: ShapeModel) -> dict[str, Any] | None:
    if shape.kind != "picture" or not shape.image_part_name:
        return None
    return {
        # The server route that serves the bytes keys on the shape's uid, not
        # this name -- a part can back more than one picture -- but the part
        # name and hash are included so a caller can tell two pictures apart
        # without fetching either one.
        "part_name": shape.image_part_name,
        "sha1": shape.image_sha1,
        "pixel_width": shape.image_pixel_width,
        "pixel_height": shape.image_pixel_height,
    }
