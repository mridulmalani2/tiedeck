"""The deck data model.

Rules and derivers read this and nothing else. They must never import
``python-pptx``, never read a raw ``a:rPr``, and never touch an unresolved
property. If a rule needs something the model does not expose, the model is
extended -- that is the whole point of the boundary. Without it, every rule
reimplements inheritance slightly differently and the tool quietly disagrees with
itself.

Every geometric value here is in points, in true slide space, with group
transforms already applied.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from tieout.model.inherit import ResolvedFill, ResolvedFont, ResolvedLine
from tieout.model.package import PackageInfo
from tieout.model.units import rect_area_pt2, rotated_bbox_pt

#: Shape kinds TieOut distinguishes. ``smartart`` is listed but never inspected:
#: its geometry lives in a diagram part TieOut does not model, and pretending
#: otherwise would produce confident nonsense.
SHAPE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "autoshape",
        "textbox",
        "picture",
        "table",
        "chart",
        "group",
        "connector",
        "freeform",
        "smartart",
        "ole",
        "media",
        "placeholder",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class ShapeRef:
    """Stable identity for a shape, as a human would describe it.

    ``slide_index`` is 1-based to match what PowerPoint's status bar shows. A
    finding that says "slide 0" is a finding a banker cannot act on.
    """

    slide_index: int
    shape_id: int
    name: str
    group_path: tuple[str, ...] = ()

    def __str__(self) -> str:
        if self.group_path:
            return f"slide {self.slide_index}: {' > '.join(self.group_path)} > {self.name}"
        return f"slide {self.slide_index}: {self.name}"

    @property
    def display_name(self) -> str:
        if self.group_path:
            return f"{' > '.join(self.group_path)} > {self.name}"
        return self.name


@dataclass(frozen=True, slots=True)
class TextRun:
    """One run of text with its font fully resolved."""

    text: str
    font: ResolvedFont
    hyperlink: str | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)


#: How far down the canvas the title fallback looks for a headline. A title sits
#: above the body it names; below the midline it no longer does.
TITLE_BAND_SHARE: Final[float] = 0.5

#: The paragraph alignments the loader emits, spelled once.
#:
#: The loader normalised OOXML's ``ctr`` to "centre" and every consumer was left
#: to guess the spelling. :mod:`tieout.model.extent` guessed "center", so no
#: paragraph ever matched and every centred one was placed as though it were
#: left aligned -- a section divider's title was measured at the left margin
#: while it renders in the middle of the slide, which LO-002, LO-003 and LO-004
#: all then read. Naming the values here makes the two ends share a spelling,
#: and ``test_extent`` asserts that the set below is closed.
ALIGN_LEFT: Final[str] = "left"
ALIGN_RIGHT: Final[str] = "right"
ALIGN_CENTRE: Final[str] = "centre"
ALIGN_JUSTIFY: Final[str] = "justify"
ALIGN_DISTRIBUTE: Final[str] = "distribute"

#: Every value :data:`tieout.model.loader._ALIGNMENT_MAP` can produce.
ALIGNMENTS: Final[frozenset[str]] = frozenset(
    {ALIGN_LEFT, ALIGN_RIGHT, ALIGN_CENTRE, ALIGN_JUSTIFY, ALIGN_DISTRIBUTE}
)

#: Alignments that can put ink anywhere across the frame, so narrowing to one
#: edge would be a guess.
SPREAD_ALIGNMENTS: Final[frozenset[str]] = frozenset(
    {ALIGN_JUSTIFY, ALIGN_DISTRIBUTE}
)


@dataclass(frozen=True, slots=True)
class TextParagraph:
    """One paragraph, with resolved runs and its indent level."""

    runs: tuple[TextRun, ...]
    level: int
    alignment: str | None = None
    #: ``none`` for an explicit no-bullet, ``char`` for a glyph bullet,
    #: ``auto`` for an auto-numbered list, or None when inherited.
    bullet_kind: str | None = None
    bullet_char: str | None = None
    bullet_autonum_type: str | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    line_spacing: float | None = None
    indent_pt: float | None = None
    margin_left_pt: float | None = None
    margin_right_pt: float | None = None

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def is_bulleted(self) -> bool:
        """Whether this paragraph renders with a bullet or a number.

        ``None`` means inherited, and an inherited bullet at level 0 in a body
        placeholder is almost always present, so an indent level above zero is
        treated as bulleted. TY-003 depends on this and it is the rule's known
        false-positive mode: a deliberately unbulleted indented paragraph in a
        body placeholder is counted as a bullet.
        """
        if self.bullet_kind == "none":
            return False
        if self.bullet_kind in ("char", "auto"):
            return True
        return self.level > 0

    @property
    def dominant_font(self) -> ResolvedFont | None:
        """The font covering the most characters in this paragraph."""
        if not self.runs:
            return None
        return max(self.runs, key=lambda r: r.char_count).font


@dataclass(frozen=True, slots=True)
class TableCell:
    """One table cell. Merged continuation cells are marked, not dropped, so
    column-wise analysis keeps its indexing."""

    row: int
    column: int
    paragraphs: tuple[TextParagraph, ...]
    row_span: int = 1
    column_span: int = 1
    is_merge_continuation: bool = False
    fill: ResolvedFill | None = None

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs).strip()


@dataclass(frozen=True, slots=True)
class TableModel:
    """A table, addressable by row and column."""

    row_count: int
    column_count: int
    cells: tuple[TableCell, ...]
    column_widths_pt: tuple[float, ...] = ()
    row_heights_pt: tuple[float, ...] = ()
    first_row_is_header: bool = False
    first_column_is_header: bool = False
    banded_rows: bool = False

    def cell(self, row: int, column: int) -> TableCell | None:
        for candidate in self.cells:
            if candidate.row == row and candidate.column == column:
                return candidate
        return None

    def column_cells(self, column: int, *, skip_header: bool = True) -> list[TableCell]:
        """Body cells of one column, header excluded by default.

        Number-format analysis must exclude the header, or a column headed
        "FY2024E" is read as a value with no decimal places.
        """
        start = 1 if (skip_header and self.first_row_is_header) else 0
        return [
            cell
            for cell in self.cells
            if cell.column == column
            and cell.row >= start
            and not cell.is_merge_continuation
        ]

    def iter_columns(
        self, *, skip_header: bool = True
    ) -> Iterator[tuple[int, list[TableCell]]]:
        for column in range(self.column_count):
            yield column, self.column_cells(column, skip_header=skip_header)

    @property
    def all_paragraphs(self) -> Iterator[TextParagraph]:
        for cell in self.cells:
            if cell.is_merge_continuation:
                continue
            yield from cell.paragraphs


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str | None
    point_count: int
    #: Whether this series carries data labels of its own. Held per series
    #: because a chart labelling one series and not another is a chart whose
    #: reader cannot compare them.
    has_data_labels: bool = False
    #: The label number format, where the series states one. Two series
    #: formatted to different precision is a defect a reader sees immediately.
    label_number_format: str | None = None
    #: The series' explicit fill, resolved to sRGB. ``None`` where the series
    #: carries no fill of its own and takes the theme's chart colour cycle,
    #: which TieOut does not model: it is set by the template rather than
    #: chosen by the author, and guessing at it would report a deck for a
    #: colour nobody in it picked.
    fill_hex: str | None = None


@dataclass(frozen=True, slots=True)
class ChartModel:
    """A chart, at the depth TieOut can measure deterministically.

    TieOut does not model chart layout. It reads the chart's text, its series
    identities and whether the conventional furniture (title, axis titles, data
    labels) is present, because those are the things a house style prescribes.
    """

    chart_type: str
    has_title: bool
    title_text: str | None
    series: tuple[ChartSeries, ...]
    categories: tuple[str, ...]
    has_data_labels: bool
    has_legend: bool
    axis_titles: tuple[str, ...]
    #: Every font resolved anywhere in the chart part, for palette and font checks.
    fonts: tuple[ResolvedFont, ...] = ()
    #: Every text string in the chart part, for placeholder-marker scanning.
    text_strings: tuple[str, ...] = ()
    #: Manual value-axis bounds, where the author pinned them. A bar chart whose
    #: baseline is not zero exaggerates every difference on it, which is why the
    #: minimum is worth modelling separately from the rest of the scaling.
    value_axis_minimum: float | None = None
    value_axis_maximum: float | None = None
    #: Whether the value axis is drawn at all. A deleted axis is a deliberate
    #: choice on a labelled chart and a problem on an unlabelled one.
    has_value_axis: bool = True

    @property
    def is_baseline_sensitive(self) -> bool:
        """Whether this chart type is read by comparing bar lengths.

        A bar read against a truncated baseline misleads in proportion to how
        much was cut off; a line chart zoomed to its range is ordinary practice
        and often the only legible option.
        """
        return self.chart_type in ("bar", "col", "barChart", "colChart")


@dataclass
class ShapeModel:
    """One shape, with every property TieOut measures already resolved."""

    ref: ShapeRef
    kind: str
    left_pt: float
    top_pt: float
    width_pt: float
    height_pt: float
    rotation: float = 0.0
    flip_horizontal: bool = False
    flip_vertical: bool = False

    effective_font: ResolvedFont | None = None
    effective_fill: ResolvedFill | None = None
    effective_line: ResolvedLine | None = None

    is_placeholder: bool = False
    placeholder_type: str | None = None
    placeholder_idx: int | None = None

    text_frame_paragraphs: tuple[TextParagraph, ...] = ()
    has_text_frame: bool = False
    word_wrap: bool | None = None
    autofit: str | None = None
    inset_left_pt: float = 7.2
    inset_right_pt: float = 7.2
    inset_top_pt: float = 3.6
    inset_bottom_pt: float = 3.6
    vertical_anchor: str | None = None
    text_direction: str | None = None
    #: ``a:bodyPr/@numCol`` and ``@spcCol``: a body laid out in columns wraps
    #: each paragraph at the column's width, not the frame's.
    text_columns: int = 1
    column_spacing_pt: float = 0.0

    image_sha1: str | None = None
    image_part_name: str | None = None
    image_pixel_width: int | None = None
    image_pixel_height: int | None = None

    #: The labels a SmartArt graphic displays. Held apart from
    #: ``text_frame_paragraphs`` deliberately: the text is real and checkable,
    #: but the diagram's internal geometry is not modelled, so a rule that
    #: measures boxes must not start treating this shape as a text frame.
    diagram_text: tuple[str, ...] = ()
    table: TableModel | None = None
    chart: ChartModel | None = None
    children: tuple[ShapeModel, ...] = ()

    #: Draw order within its parent tree. Higher paints later, i.e. on top.
    z_order: int = 0
    #: The layout placeholder's geometry, when this shape is a placeholder, so
    #: BR-008 can measure drift without re-walking the layout.
    layout_geometry_pt: tuple[float, float, float, float] | None = None

    raw_element: Any = field(default=None, repr=False)

    # -- geometry ----------------------------------------------------------------

    @property
    def bbox_pt(self) -> tuple[float, float, float, float]:
        """Stored geometry as ``(left, top, width, height)``, ignoring rotation."""
        return (self.left_pt, self.top_pt, self.width_pt, self.height_pt)

    @property
    def visual_bbox_pt(self) -> tuple[float, float, float, float]:
        """Axis-aligned bounding box including rotation. What a reader sees."""
        return rotated_bbox_pt(
            self.left_pt, self.top_pt, self.width_pt, self.height_pt, self.rotation
        )

    @property
    def right_pt(self) -> float:
        return self.left_pt + self.width_pt

    @property
    def bottom_pt(self) -> float:
        return self.top_pt + self.height_pt

    @property
    def centre_x_pt(self) -> float:
        return self.left_pt + self.width_pt / 2.0

    @property
    def centre_y_pt(self) -> float:
        return self.top_pt + self.height_pt / 2.0

    @property
    def area_pt2(self) -> float:
        return rect_area_pt2(self.bbox_pt)

    @property
    def aspect_ratio(self) -> float | None:
        if self.height_pt <= 0:
            return None
        return self.width_pt / self.height_pt

    # -- text --------------------------------------------------------------------

    @property
    def text(self) -> str:
        """All text in the shape, including table, chart and SmartArt text."""
        parts = [p.text for p in self.text_frame_paragraphs]
        if self.table is not None:
            parts.extend(p.text for p in self.table.all_paragraphs)
        if self.chart is not None:
            parts.extend(self.chart.text_strings)
        parts.extend(self.diagram_text)
        return "\n".join(part for part in parts if part)

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def char_count(self) -> int:
        return len(self.text.replace("\n", ""))

    @property
    def all_paragraphs(self) -> tuple[TextParagraph, ...]:
        paragraphs = list(self.text_frame_paragraphs)
        if self.table is not None:
            paragraphs.extend(self.table.all_paragraphs)
        return tuple(paragraphs)

    @property
    def fonts(self) -> tuple[ResolvedFont, ...]:
        """Every resolved font in the shape, weighted analysis aside."""
        out: list[ResolvedFont] = []
        for paragraph in self.all_paragraphs:
            out.extend(run.font for run in paragraph.runs)
        if self.chart is not None:
            out.extend(self.chart.fonts)
        if not out and self.effective_font is not None:
            out.append(self.effective_font)
        return tuple(out)

    @property
    def is_empty_placeholder(self) -> bool:
        """A visible placeholder with no content. HY-006's target.

        Pictures, tables and charts count as content even with no text.
        """
        if not self.is_placeholder:
            return False
        if self.table is not None or self.chart is not None:
            return False
        if self.kind in ("picture", "media", "ole"):
            return False
        return not self.text.strip()

    # -- traversal ---------------------------------------------------------------

    def walk(self) -> Iterator[ShapeModel]:
        """This shape then all descendants, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def is_container(self) -> bool:
        return self.kind == "group"


@dataclass
class SlideModel:
    """One slide, with its shapes flattened into true slide space."""

    index: int
    shapes: tuple[ShapeModel, ...]
    width_pt: float
    height_pt: float
    layout_name: str | None = None
    layout_type: str | None = None
    master_name: str | None = None
    notes_text: str = ""
    is_hidden: bool = False
    background: ResolvedFill | None = None
    archetype: str = "unknown"
    archetype_confidence: str = "low"
    archetype_reasons: tuple[str, ...] = ()
    part_name: str | None = None

    # -- traversal ---------------------------------------------------------------

    def all_shapes(self) -> Iterator[ShapeModel]:
        """Every shape including group descendants, depth first."""
        for shape in self.shapes:
            yield from shape.walk()

    def leaf_shapes(self) -> Iterator[ShapeModel]:
        """Every shape that is not a group. Groups carry no visual of their own."""
        for shape in self.all_shapes():
            if not shape.is_container:
                yield shape

    # -- convenience -------------------------------------------------------------

    @property
    def tables(self) -> tuple[ShapeModel, ...]:
        return tuple(s for s in self.all_shapes() if s.table is not None)

    @property
    def charts(self) -> tuple[ShapeModel, ...]:
        return tuple(s for s in self.all_shapes() if s.chart is not None)

    @property
    def pictures(self) -> tuple[ShapeModel, ...]:
        return tuple(s for s in self.all_shapes() if s.kind == "picture")

    @property
    def text_shapes(self) -> tuple[ShapeModel, ...]:
        return tuple(s for s in self.leaf_shapes() if s.has_text)

    @property
    def title_shape(self) -> ShapeModel | None:
        """The title placeholder, or the largest short text shape near the top.

        Real decks frequently replace the title placeholder with a plain text box
        so the designer can control the rule beneath it. A title check that only
        looks at placeholders silently passes those slides.

        The fallback searches the top half rather than the top third. A content
        slide sets its headline against the top, but a title slide and a section
        divider set theirs down the page on purpose -- the client deck this was
        found on puts them at 34% and 45% of the canvas. The top third contained
        nothing but the logo lockup on those slides, so the monogram won the slot
        by default and the report named two slides "H". The classifier reads the
        title as well, which filed a plainly marked divider as a content slide.

        Half the canvas is as far as this can go: a title sits above the body it
        names, and below the midline it no longer does. Widening the band does
        not change which shape is chosen anywhere the old band found a real
        headline, because the choice is still the largest type on offer.
        """
        for shape in self.all_shapes():
            if shape.placeholder_type in ("title", "ctrTitle"):
                return shape
        candidates = [
            s
            for s in self.text_shapes
            if s.top_pt < self.height_pt * TITLE_BAND_SHARE and s.char_count <= 200
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda s: (s.effective_font.size_pt or 0.0 if s.effective_font else 0.0),
        )

    @property
    def title_text(self) -> str:
        title = self.title_shape
        return title.text.strip() if title else ""

    @property
    def text_length(self) -> int:
        return sum(s.char_count for s in self.leaf_shapes())

    @property
    def area_pt2(self) -> float:
        return self.width_pt * self.height_pt

    @property
    def placeholder_signature(self) -> tuple[str, ...]:
        """The sorted multiset of placeholder types on the slide.

        This is the strongest single archetype signal, because a deck's layouts
        are what its designer used to express intent.
        """
        return tuple(
            sorted(
                s.placeholder_type or "body"
                for s in self.all_shapes()
                if s.is_placeholder
            )
        )

    def content_bbox_pt(
        self, exclude: frozenset[int] = frozenset()
    ) -> tuple[float, float, float, float] | None:
        """Union bounding box of content shapes, excluding the given shape ids."""
        boxes = [
            s.visual_bbox_pt
            for s in self.leaf_shapes()
            if s.ref.shape_id not in exclude and s.width_pt > 0 and s.height_pt > 0
        ]
        if not boxes:
            return None
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[0] + b[2] for b in boxes)
        bottom = max(b[1] + b[3] for b in boxes)
        return (left, top, right - left, bottom - top)


@dataclass(eq=False)
class DeckModel:
    """A whole deck, plus the package-level facts the hygiene rules need.

    ``eq=False`` so the model keeps identity semantics and stays hashable. Two
    separately loaded copies of the same file are different decks -- they carry
    their own mutable archetype assignments -- and value equality over several
    thousand shapes would be both wrong and ruinously slow. Being hashable also
    lets the rule engine memoise per-deck work in a weak-keyed cache.
    """

    path: Path
    slides: tuple[SlideModel, ...]
    width_pt: float
    height_pt: float
    package: PackageInfo
    theme_major_font: str | None = None
    theme_minor_font: str | None = None
    #: Slide indices that PowerPoint will skip in presentation mode.
    hidden_slide_indices: tuple[int, ...] = ()
    #: Layout name -> the ``type`` attribute of that layout, for archetype signals.
    layout_types: dict[str, str] = field(default_factory=dict)

    @property
    def slide_count(self) -> int:
        return len(self.slides)

    @property
    def aspect_ratio(self) -> float:
        return self.width_pt / self.height_pt if self.height_pt else 0.0

    def slide(self, index: int) -> SlideModel | None:
        """Fetch by 1-based index."""
        for candidate in self.slides:
            if candidate.index == index:
                return candidate
        return None

    def all_shapes(self) -> Iterator[tuple[SlideModel, ShapeModel]]:
        for slide in self.slides:
            for shape in slide.all_shapes():
                yield slide, shape

    def leaf_shapes(self) -> Iterator[tuple[SlideModel, ShapeModel]]:
        for slide in self.slides:
            for shape in slide.leaf_shapes():
                yield slide, shape

    @property
    def fonts_used(self) -> dict[str, int]:
        """Resolved typeface -> character count, deck-wide."""
        counts: dict[str, int] = {}
        for slide in self.slides:
            for shape in slide.leaf_shapes():
                for paragraph in shape.all_paragraphs:
                    for run in paragraph.runs:
                        if run.font.name:
                            counts[run.font.name] = counts.get(run.font.name, 0) + len(
                                run.text
                            )
        return counts

    def slides_by_archetype(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for slide in self.slides:
            out.setdefault(slide.archetype, []).append(slide.index)
        return out

    @property
    def image_sha1_slide_support(self) -> dict[str, set[int]]:
        """Image SHA1 -> the set of slide indices it appears on. Logo detection
        is built directly on this."""
        out: dict[str, set[int]] = {}
        for slide in self.slides:
            for shape in slide.all_shapes():
                if shape.image_sha1:
                    out.setdefault(shape.image_sha1, set()).add(slide.index)
        return out
