"""Observation collection: typed, scoped, weighted.

Every deriver speaks in :class:`Observation` records rather than reaching into
the deck itself. That indirection buys three things worth the extra layer:

* **Scope discipline.** The same key means different things at different scopes.
  "Title capitalisation is sentence case" is true at ``archetype:content`` and
  meaningless deck-wide, because a section divider's heading follows a different
  convention. Forcing every observation to declare its scope makes that explicit
  rather than accidental.
* **Correct weighting.** A colour's importance is the area it covers; a font's is
  the characters it sets. Counting shapes instead would let a 4pt hairline rule
  outvote a full-bleed background.
* **Auditable provenance.** Every number in an emitted profile has to be
  traceable to an observation count. If the provenance comment cannot be written,
  the value must not be emitted -- so the counts have to survive to emission time.

This module also owns the shared traversal helpers, so the brand, layout and
typography derivers all walk the deck the same way and all agree on which shapes
are furniture and what role each piece of text plays.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Final

from tieout.model.deck import (
    DeckModel,
    ShapeModel,
    SlideModel,
    TableCell,
    TableModel,
    TextParagraph,
    TextRun,
)
from tieout.model.furniture import Furniture, content_shapes, font_role

#: Scope for a fact that holds across the whole deck.
DECK: Final[str] = "deck"


def archetype_scope(archetype: str) -> str:
    return f"archetype:{archetype}"


def table_column_scope(slide_index: int, uid: int, column: int) -> str:
    return f"table:{slide_index}:{uid}:col:{column}"


@dataclass(frozen=True, slots=True)
class Observation:
    """One measurement, with where it came from and how much it counts for."""

    key: str
    scope: str
    value: Any
    slide_index: int
    weight: float = 1.0
    #: Optional identity of the shape observed, so an outlier question can name it.
    uid: int | None = None
    shape_name: str | None = None

    @property
    def group(self) -> tuple[str, str]:
        return (self.key, self.scope)


@dataclass
class ObservationSet:
    """A collection of observations, grouped on demand."""

    items: list[Observation] = field(default_factory=list)

    def add(
        self,
        key: str,
        scope: str,
        value: Any,
        slide_index: int,
        weight: float = 1.0,
        *,
        uid: int | None = None,
        shape_name: str | None = None,
    ) -> None:
        if weight <= 0:
            return
        self.items.append(
            Observation(
                key=key,
                scope=scope,
                value=value,
                slide_index=slide_index,
                weight=weight,
                uid=uid,
                shape_name=shape_name,
            )
        )

    def extend(self, other: ObservationSet) -> None:
        self.items.extend(other.items)

    def __len__(self) -> int:
        return len(self.items)

    def groups(self) -> dict[tuple[str, str], list[Observation]]:
        out: dict[tuple[str, str], list[Observation]] = {}
        for item in self.items:
            out.setdefault(item.group, []).append(item)
        return out

    def for_key(self, key: str) -> list[Observation]:
        return [item for item in self.items if item.key == key]

    def scopes_for(self, key: str) -> list[str]:
        seen: dict[str, None] = {}
        for item in self.items:
            if item.key == key:
                seen.setdefault(item.scope, None)
        return list(seen)

    def group_for(self, key: str, scope: str) -> list[Observation]:
        return [item for item in self.items if item.key == key and item.scope == scope]

    def slides_covered(self, key: str, scope: str) -> set[int]:
        return {o.slide_index for o in self.group_for(key, scope)}

    def keys(self) -> list[str]:
        seen: dict[str, None] = {}
        for item in self.items:
            seen.setdefault(item.key, None)
        return list(seen)


# --------------------------------------------------------------------------------------
# Shared traversal
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunContext:
    """One resolved run, with everything a deriver needs to scope it."""

    slide: SlideModel
    shape: ShapeModel
    paragraph: TextParagraph
    run: TextRun
    role: str
    #: Set when the run came from a table cell.
    cell: TableCell | None = None
    table: TableModel | None = None
    column: int | None = None

    @property
    def weight(self) -> float:
        """Character count. Fonts and conventions are weighted by how much text
        they actually set, not by how many shapes carry them."""
        return float(len(self.run.text.strip()))

    @property
    def is_furniture_role(self) -> bool:
        return self.role == "footnote" and self.cell is None


def iter_runs(
    deck: DeckModel,
    furniture: Furniture,
    *,
    include_furniture: bool = False,
) -> Iterator[RunContext]:
    """Every resolved text run in the deck, with its role and table position.

    Furniture is excluded by default. The confidentiality line appears on every
    slide, and letting it vote on the body font size band would drag the band
    down to 7pt on any deck with a footer.
    """
    for slide in deck.slides:
        for shape in slide.leaf_shapes():
            is_chrome = furniture.is_furniture(slide.index, shape.ref.uid)
            if is_chrome and not include_furniture:
                continue
            role = font_role(slide, shape, furniture=furniture)

            for paragraph in shape.text_frame_paragraphs:
                for run in paragraph.runs:
                    if not run.text.strip():
                        continue
                    yield RunContext(slide, shape, paragraph, run, role)

            if shape.table is not None:
                for cell in shape.table.cells:
                    if cell.is_merge_continuation:
                        continue
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            if not run.text.strip():
                                continue
                            yield RunContext(
                                slide,
                                shape,
                                paragraph,
                                run,
                                "table",
                                cell=cell,
                                table=shape.table,
                                column=cell.column,
                            )


def iter_chart_fonts(
    deck: DeckModel,
) -> Iterator[tuple[SlideModel, ShapeModel, Any]]:
    """Resolved fonts inside chart parts.

    Separate from :func:`iter_runs` because a chart's text properties are
    generated rather than typed: there is one resolved font for the whole chart,
    and attributing it to each label would count one piece of evidence six times.
    """
    for slide in deck.slides:
        for shape in slide.charts:
            if shape.chart is None:
                continue
            for resolved in shape.chart.fonts:
                yield slide, shape, resolved


def iter_content(
    deck: DeckModel, furniture: Furniture
) -> Iterator[tuple[SlideModel, ShapeModel]]:
    """Content shapes, furniture and zero-area shapes excluded."""
    for slide in deck.slides:
        for shape in content_shapes(slide, furniture):
            yield slide, shape


def learnable_slides(deck: DeckModel) -> list[SlideModel]:
    """Slides whose evidence may be used for learning.

    Section 7: anything classified ``unknown`` is excluded from learning, because
    a slide TieOut cannot categorise must not be allowed to pollute a derived
    rule. Such slides are still checked against deck-wide rules afterwards.
    """
    return [slide for slide in deck.slides if slide.archetype != "unknown"]


def archetypes_present(deck: DeckModel) -> list[str]:
    seen: dict[str, None] = {}
    for slide in learnable_slides(deck):
        seen.setdefault(slide.archetype, None)
    return sorted(seen)


def slides_of(deck: DeckModel, archetype: str) -> list[SlideModel]:
    return [s for s in deck.slides if s.archetype == archetype]
