"""Turning a deck into the smallest payload that can answer a semantic question.

Two competing pressures shape this. The model needs enough structure to tell a
headline from a table cell and to see which column a number sits under — without
that it cannot say anything useful about consistency. And the payload should
carry as little as possible, because everything in it is something that has to
be redacted correctly.

The compromise is a flat, line-oriented rendering: one line per text item,
prefixed with the slide, the archetype and the role. It is compact, it is stable
enough that two runs of the same deck produce comparable payloads, and — the
property that matters most — every line is a line, so a residual can be reported
as "line 214" and an analyst can look at it.

Speaker notes are excluded by default. They are where the sensitive material
lives: the price, the walk-away number, what the other side said. Including them
is a deliberate flag, not a default.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from tieout.model.deck import DeckModel, ShapeModel, SlideModel
from tieout.model.furniture import font_role

__all__ = ["DeckPayload", "TextItem", "extract"]

#: Shapes this small carry a page number or a divider glyph, not an argument.
_MIN_INTERESTING_CHARACTERS: Final[int] = 2


@dataclass(frozen=True, slots=True)
class TextItem:
    """One addressable piece of text, with just enough context to reason about."""

    slide_index: int
    archetype: str
    role: str
    shape: str
    text: str

    def render(self) -> str:
        return f"[s{self.slide_index}|{self.archetype}|{self.role}] {self.text}"


@dataclass(frozen=True, slots=True)
class DeckPayload:
    """Everything the model will be shown, before redaction."""

    items: tuple[TextItem, ...]
    slide_count: int

    def render(self) -> str:
        """The payload as text, one item per line.

        Lines are the addressing scheme for everything downstream: the residual
        list cites them, and the model is asked to quote them.
        """
        return "\n".join(item.render() for item in self.items)

    @property
    def characters(self) -> int:
        return len(self.render())

    def item_for_slide(self, slide_index: int) -> tuple[TextItem, ...]:
        return tuple(item for item in self.items if item.slide_index == slide_index)


def extract(deck: DeckModel, *, include_notes: bool = False) -> DeckPayload:
    """Flatten a deck into a payload.

    Ordering is slide, then z-order within the slide, so the payload reads in
    roughly the order a person would: title, then body, then the table, then the
    footnote.
    """
    items: list[TextItem] = []
    for slide in deck.slides:
        items.extend(_slide_items(slide, include_notes=include_notes))
    return DeckPayload(items=tuple(items), slide_count=deck.slide_count)


def _slide_items(slide: SlideModel, *, include_notes: bool) -> Iterator[TextItem]:
    for shape in sorted(slide.shapes, key=lambda s: (s.z_order, s.top_pt, s.left_pt)):
        yield from _shape_items(slide, shape)
    if include_notes and slide.notes_text.strip():
        yield TextItem(
            slide_index=slide.index,
            archetype=slide.archetype,
            role="notes",
            shape="Speaker notes",
            text=_flatten(slide.notes_text),
        )


def _shape_items(slide: SlideModel, shape: ShapeModel) -> Iterator[TextItem]:
    for child in shape.walk():
        if child.table is not None:
            yield from _table_items(slide, child)
            continue
        if child.chart is not None:
            yield from _chart_items(slide, child)
            continue
        text = _flatten(child.text)
        if len(text) < _MIN_INTERESTING_CHARACTERS:
            continue
        yield TextItem(
            slide_index=slide.index,
            archetype=slide.archetype,
            role=font_role(slide, child),
            shape=child.ref.display_name,
            text=text,
        )


def _table_items(slide: SlideModel, shape: ShapeModel) -> Iterator[TextItem]:
    """A table rendered row by row, pipe-separated.

    The grid has to survive: a number without its row label and column header
    cannot be compared with anything, and comparing numbers is the only reason
    this payload exists. Merge continuations are dropped so a spanned cell does
    not appear twice.
    """
    table = shape.table
    if table is None:  # pragma: no cover - guarded by the caller
        return
    grid: dict[tuple[int, int], str] = {
        (cell.row, cell.column): _flatten(cell.text)
        for cell in table.cells
        if not cell.is_merge_continuation
    }
    for row in range(table.row_count):
        values = [grid.get((row, column), "") for column in range(table.column_count)]
        if not any(value.strip() for value in values):
            continue
        yield TextItem(
            slide_index=slide.index,
            archetype=slide.archetype,
            role="table_header" if row == 0 and table.first_row_is_header else "table_row",
            shape=shape.ref.display_name,
            text=" | ".join(values),
        )


def _chart_items(slide: SlideModel, shape: ShapeModel) -> Iterator[TextItem]:
    chart = shape.chart
    if chart is None:  # pragma: no cover - guarded by the caller
        return
    parts: list[tuple[str, str]] = []
    if chart.title_text:
        parts.append(("chart_title", _flatten(chart.title_text)))
    if chart.axis_titles:
        parts.append(("chart_axis", " | ".join(_flatten(t) for t in chart.axis_titles if t)))
    if chart.categories:
        parts.append(("chart_categories", " | ".join(_flatten(c) for c in chart.categories if c)))
    series = [s.name for s in chart.series if s.name]
    if series:
        parts.append(("chart_series", " | ".join(_flatten(name) for name in series)))
    for role, text in parts:
        if len(text) >= _MIN_INTERESTING_CHARACTERS:
            yield TextItem(
                slide_index=slide.index,
                archetype=slide.archetype,
                role=role,
                shape=shape.ref.display_name,
                text=text,
            )


def _flatten(text: str) -> str:
    """One line, single-spaced.

    Newlines inside a shape become spaces because the payload's addressing is
    one item per line. The typographic detail a line break carries is TieOut's
    own business and the model has no use for it.
    """
    return " ".join(text.split())
