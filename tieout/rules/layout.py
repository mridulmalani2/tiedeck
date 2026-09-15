"""Layout rules: canvas containment, margins, alignment, overlap, fit and gutters.

Everything here is measured from :class:`ShapeModel` geometry in points, in true
slide space, with group transforms and rotation already resolved by the model
layer. No rule in this module imports another rule module, imports python-pptx,
or reads an unresolved property.

Three module-wide decisions, stated once rather than in eight docstrings:

* **Furniture is excluded from the frame-relative rules.** The logo, the page
  number and the confidentiality line deliberately sit outside the content
  frame, so LO-002, LO-003, LO-004, LO-007 and LO-008 work from
  :func:`content_shapes`. LO-001 does not exclude it: a logo half off the canvas
  is a blocker whoever put it there.
* **Clustering happens inside each rule.** A row of five cards nudged off the
  grid is one problem, and reporting it five times trains the reader to skim the
  report. Each rule collapses its own observations on the axis that makes them
  one problem, and only then hands the remainder to :func:`cluster_findings` as
  a per-slide backstop.
* **An expectation that was never learned produces no finding.** Rules declare
  their profile paths in ``requires`` so the engine can skip them wholesale;
  where the gap is per-slide or per-archetype rather than per-deck, the rule
  records it in ``unchecked`` instead, because silence that looks like a pass is
  the worst thing a QA tool can do.
"""

from __future__ import annotations

import functools
import itertools
import re
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final

from PIL import ImageFont

from tieout.cluster import cluster_values
from tieout.model.deck import DeckModel, ShapeModel, ShapeRef, SlideModel, TextParagraph
from tieout.model.furniture import Furniture, content_shapes, detect_furniture, font_role
from tieout.model.units import rect_intersection_area_pt2
from tieout.profile.schema import (
    Box,
    Confidence,
    GridProfile,
    Profile,
    RecurringElement,
    Severity,
)
from tieout.rules.base import Finding, Rule, cluster_findings, register

#: Rounding slack for canvas containment. PowerPoint stores EMU, and a shape
#: dragged flush against the edge routinely lands a fraction of a point outside.
CANVAS_EPSILON_PT: Final[float] = 0.5

#: A shape covering at least this share of the slide is a deliberate full bleed
#: and is not measured against the safe margin.
FULL_BLEED_AREA_SHARE: Final[float] = 0.80

#: Shapes thinner than this in either dimension are rules, dividers and
#: underscores. They legitimately sit beneath text, so LO-004 ignores them.
THIN_SHAPE_PT: Final[float] = 3.0

#: A row or a column needs this many siblings before its gutters mean anything:
#: two shapes have one gutter and therefore no spread to measure.
MIN_SIBLINGS: Final[int] = 3

#: Slack when comparing required text height against available text height.
OVERFLOW_EPSILON_PT: Final[float] = 0.5


# --------------------------------------------------------------------------------------
# Shared geometry helpers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _EdgeValue:
    """One measurable alignment reference of one shape."""

    label: str
    #: ``"x"`` for edges that align to grid columns, ``"y"`` for grid rows.
    axis: str
    value: float


def _edge_values(shape: ShapeModel) -> tuple[_EdgeValue, ...]:
    """The six references a designer actually snaps a shape to."""
    return (
        _EdgeValue("left", "x", shape.left_pt),
        _EdgeValue("right", "x", shape.right_pt),
        _EdgeValue("centre-x", "x", shape.centre_x_pt),
        _EdgeValue("top", "y", shape.top_pt),
        _EdgeValue("bottom", "y", shape.bottom_pt),
        _EdgeValue("centre-y", "y", shape.centre_y_pt),
    )


def _nearest_grid_line(grid: GridProfile, axis: str, value: float) -> float | None:
    return grid.nearest_column(value) if axis == "x" else grid.nearest_row(value)


def _on_grid(grid: GridProfile, axis: str, value: float) -> bool:
    return grid.on_column(value) if axis == "x" else grid.on_row(value)


def _is_thin(shape: ShapeModel) -> bool:
    return shape.width_pt < THIN_SHAPE_PT or shape.height_pt < THIN_SHAPE_PT


def _canvas(slide: SlideModel, deck: DeckModel) -> tuple[float, float]:
    """The slide's own canvas, falling back to the presentation size."""
    width = slide.width_pt if slide.width_pt > 0 else deck.width_pt
    height = slide.height_pt if slide.height_pt > 0 else deck.height_pt
    return (width, height)


def _furniture_of(rule: Rule, deck: DeckModel, profile: Profile) -> Furniture:
    """Furniture for this deck, memoised by the engine where the engine can.

    ``Rule.furniture`` is the right entry point and is used first. It currently
    memoises in a :class:`weakref.WeakKeyDictionary` keyed on the deck, which
    raises :class:`TypeError` because :class:`DeckModel` is a plain dataclass
    and therefore unhashable. Detecting furniture directly is the same
    computation without the cache, so a broken cache costs time rather than
    correctness -- and a layout rule that cannot tell the logo from a column is
    worse than a slow one. Remove this fallback once the cache can key on a
    deck.
    """
    try:
        return rule.furniture(deck, profile)
    except TypeError:
        return detect_furniture(deck, profile)


# --------------------------------------------------------------------------------------
# LO-001
# --------------------------------------------------------------------------------------


@register
class ShapeOffCanvas(Rule):
    """Reports a shape whose visible bounding box extends outside the slide canvas.

    Measures :attr:`ShapeModel.visual_bbox_pt` -- the rotation-aware box, because
    PowerPoint rotates a shape about its own centre without touching its stored
    ``off``/``ext``, so a rotated shape can spill off the canvas while its stored
    geometry sits comfortably inside it -- against the slide's own width and
    height, allowing :data:`CANVAS_EPSILON_PT` for EMU rounding. The canvas
    compared against is the deck's own, not the profile's: a deck built at the
    wrong page size is BR-009's finding, not this one's. Every offending edge of
    a shape is reported in one finding, and findings cluster per slide, because
    a group dragged off the right edge is one drag.

    Known false-positive mode: a shape deliberately bled off the canvas -- a
    half-visible background graphic, a photograph cropped by the slide edge -- is
    reported, because nothing in the file distinguishes that intent from an
    accident.
    """

    id: ClassVar[str] = "LO-001"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "blocker"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A shape extends outside the slide canvas"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for slide in deck.slides:
            width, height = _canvas(slide, deck)
            if width <= 0 or height <= 0:
                self.note_unchecked(slide.index, "the slide has no usable canvas size")
                continue
            for shape in slide.leaf_shapes():
                if shape.width_pt <= 0 or shape.height_pt <= 0:
                    continue
                left, top, box_width, box_height = shape.visual_bbox_pt
                spills = [
                    (edge, overflow, measured, limit)
                    for edge, overflow, measured, limit in (
                        ("left", -left, left, 0.0),
                        ("top", -top, top, 0.0),
                        ("right", left + box_width - width, left + box_width, width),
                        ("bottom", top + box_height - height, top + box_height, height),
                    )
                    if overflow > CANVAS_EPSILON_PT
                ]
                if not spills:
                    continue
                spills.sort(key=lambda item: -item[1])
                edge, _, measured, limit = spills[0]
                described = ", ".join(f"{name} by {over:.1f}pt" for name, over, _, _ in spills)
                rotated = f", rotated {shape.rotation:g} degrees" if shape.rotation else ""
                findings.append(
                    self.finding(
                        where=shape.ref,
                        profile=profile,
                        provenance_path="slide",
                        message=(
                            f"{shape.ref.name} extends off the {width:g}x{height:g}pt "
                            f"canvas: {described}{rotated}"
                        ),
                        measured=f"{edge} edge at {measured:.1f}pt",
                        expected=f"{edge} edge within {limit:g}pt",
                        remedy="Move the shape back onto the canvas",
                        bbox_pt=shape.visual_bbox_pt,
                    )
                )
        return cluster_findings(findings)


# --------------------------------------------------------------------------------------
# LO-002
# --------------------------------------------------------------------------------------


@register
class MarginIntrusion(Rule):
    """Reports a content shape intruding into the learned safe margin for its archetype.

    Measures each content shape's rotation-aware bounding box against
    ``profile.layout.safe_margin_pt[archetype]``, allowing
    ``profile.layout.position_tolerance_pt``. An archetype absent from the
    mapping is not checked and is recorded in ``unchecked``, because "no evidence
    either way" must not produce the same report line as "in breach". Furniture
    is excluded -- the logo and the footer sit outside the content frame by
    design -- and so are full-bleed shapes covering at least
    :data:`FULL_BLEED_AREA_SHARE` of the slide, which are backgrounds rather
    than content.

    Known false-positive mode: a deliberate full-width device that is not quite
    full-bleed -- a tinted band spanning the slide at a third of its height --
    reads as a content shape breaking the left and right margins.
    """

    id: ClassVar[str] = "LO-002"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A shape intrudes into the learned safe margin"
    requires: ClassVar[tuple[str, ...]] = ("layout.safe_margin_pt",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        margins = profile.layout.safe_margin_pt
        tolerance = profile.layout.position_tolerance_pt
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []

        for slide in deck.slides:
            margin = margins.get(slide.archetype)
            if margin is None:
                self.note_unchecked(
                    slide.index,
                    f"no safe margin was learned for the {slide.archetype} archetype",
                )
                continue
            width, height = _canvas(slide, deck)
            full_bleed = FULL_BLEED_AREA_SHARE * width * height
            for shape in content_shapes(slide, furniture):
                if full_bleed > 0 and shape.area_pt2 >= full_bleed:
                    continue
                left, top, box_width, box_height = shape.visual_bbox_pt
                breaches = [
                    (edge, intrusion, measured, limit)
                    for edge, intrusion, measured, limit in (
                        ("left", margin.left - left, left, margin.left),
                        ("top", margin.top - top, top, margin.top),
                        (
                            "right",
                            (left + box_width) - (width - margin.right),
                            left + box_width,
                            width - margin.right,
                        ),
                        (
                            "bottom",
                            (top + box_height) - (height - margin.bottom),
                            top + box_height,
                            height - margin.bottom,
                        ),
                    )
                    if intrusion > tolerance
                ]
                if not breaches:
                    continue
                breaches.sort(key=lambda item: -item[1])
                edge, _, measured, limit = breaches[0]
                described = ", ".join(f"{name} by {by:.1f}pt" for name, by, _, _ in breaches)
                findings.append(
                    self.finding(
                        where=shape.ref,
                        profile=profile,
                        provenance_path=f"layout.safe_margin_pt.{slide.archetype}",
                        message=(
                            f"{shape.ref.name} intrudes into the safe margin for "
                            f"{slide.archetype} slides: {described}"
                        ),
                        measured=f"{edge} edge at {measured:.1f}pt",
                        expected=f"{edge} edge at {limit:g}pt",
                        remedy=f"Move the {edge} edge to {limit:g}pt or further in",
                        bbox_pt=shape.visual_bbox_pt,
                    )
                )
        return cluster_findings(findings)


# --------------------------------------------------------------------------------------
# LO-003
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NearMiss:
    """One shape edge that just misses a learned grid line."""

    ref: ShapeRef
    label: str
    axis: str
    value: float
    line: float
    bbox: tuple[float, float, float, float]

    @property
    def delta(self) -> float:
        return self.value - self.line

    @property
    def line_kind(self) -> str:
        return "column" if self.axis == "x" else "row"


@register
class NearMissAlignment(Rule):
    """Reports a shape edge sitting just off a learned grid line.

    **Interpretation of the specification.** Section 9 describes this rule as
    "two shapes whose left, right, top, bottom, centre-x or centre-y differ by a
    value inside ``near_miss_alignment_pt`` but not zero", weighted higher "when
    one edge sits on a learned grid line and the other does not". Taken
    literally, that predicate fires on dozens of coincidental pairs on any real
    deck and almost none of them is a defect. Section 8.3 settles it: "a shape
    3pt off a real grid line is a defect while a shape 3pt off a one-off edge is
    not". Only the weighted case is therefore reported, and the on-grid side of
    the comparison is the grid line itself -- a learned position that shapes
    across the deck demonstrably do sit on. An edge is reported when it

    * is **not** on any learned grid line, that is, further than
      ``profile.layout.grid.tolerance_pt`` from the nearest one, and
    * misses that nearest line by a distance inside
      ``profile.layout.near_miss_alignment_pt``.

    The off-grid shape is the offender and the grid line is the expectation.
    Like is compared with like: horizontal edges only against grid columns,
    vertical edges only against grid rows, never a left against a right.

    Observations are grouped by slide and grid line, so a row of five cards
    nudged together yields one finding; groups on the same slide sharing the same
    signed offset are then merged, because one shape dragged 3pt off two grid
    lines at once is one drag and not two defects.

    Known false-positive mode: a deliberate small offset -- a shadow layer, a
    highlight bar intentionally set 2pt inside its card, an optical correction on
    a heavy glyph -- sits in exactly the window this rule treats as a mistake.
    """

    id: ClassVar[str] = "LO-003"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A shape edge sits just off a learned grid line"
    requires: ClassVar[tuple[str, ...]] = ("layout.grid", "layout.near_miss_alignment_pt")

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        grid = profile.layout.grid
        if not grid.columns_pt and not grid.rows_pt:
            return self.skip("the learned grid has neither columns nor rows")
        window = profile.layout.near_miss_alignment_pt
        furniture = self.furniture(deck, profile)

        observed: list[_NearMiss] = []
        for slide in deck.slides:
            for shape in content_shapes(slide, furniture):
                for edge in _edge_values(shape):
                    if _on_grid(grid, edge.axis, edge.value):
                        continue
                    line = _nearest_grid_line(grid, edge.axis, edge.value)
                    if line is None or not window.contains(edge.value - line):
                        continue
                    observed.append(
                        _NearMiss(
                            ref=shape.ref,
                            label=edge.label,
                            axis=edge.axis,
                            value=edge.value,
                            line=line,
                            bbox=shape.bbox_pt,
                        )
                    )

        return [self._finding_for(group, profile) for group in _group_near_misses(observed)]

    def _finding_for(self, group: Sequence[_NearMiss], profile: Profile) -> Finding:
        first = group[0]
        offenders = sorted({miss.ref.display_name for miss in group})
        direction = "past" if first.delta > 0 else "short of"
        if len(offenders) == 1:
            detail = "; ".join(
                f"{label} at {value:g}pt against the {line:g}pt {kind}"
                for label, value, line, kind in sorted(
                    {(m.label, m.value, m.line, m.line_kind) for m in group}
                )
            )
            message = (
                f"{offenders[0]} sits {abs(first.delta):.1f}pt {direction} the learned "
                f"grid: {detail}"
            )
        else:
            lines = ", ".join(
                f"{line:g}pt {kind}"
                for line, kind in sorted({(m.line, m.line_kind) for m in group})
            )
            message = (
                f"{len(offenders)} shapes sit {abs(first.delta):.1f}pt {direction} the "
                f"learned grid ({lines}): {', '.join(offenders)}"
            )
        return self.finding(
            where=first.ref,
            profile=profile,
            provenance_path="layout.grid",
            message=message,
            measured=f"{first.label} {first.value:g}pt",
            expected=f"{first.line:g}pt",
            # Deliberately not naming the line. The remedy is the grouping key
            # in the review note, and five shapes each a point off five
            # different grid lines is one job -- "snap these to the grid" --
            # not five. The line each one wants is in `expected`.
            remedy="Snap the edge to the grid line it is nearly on",
            bbox_pt=first.bbox,
        )


def _group_near_misses(observed: Iterable[_NearMiss]) -> list[list[_NearMiss]]:
    """Group near misses by slide and grid line, then merge equal offsets.

    Two stages because they answer two different questions. Grouping by grid line
    is what turns five cards nudged off one column into one finding. Merging
    equal signed offsets afterwards is what stops a single shape dragged 3pt
    right being reported once for its left edge and again for its right.
    """
    by_line: dict[tuple[int, str, float], list[_NearMiss]] = {}
    for miss in observed:
        by_line.setdefault((miss.ref.slide_index, miss.axis, miss.line), []).append(miss)

    merged: dict[tuple[int, float], list[_NearMiss]] = {}
    for key in sorted(by_line):
        group = by_line[key]
        # Signed and rounded: 3pt right and 3pt left are two different mistakes.
        merged.setdefault((key[0], round(group[0].delta, 2)), []).extend(group)
    return [merged[key] for key in sorted(merged)]


# --------------------------------------------------------------------------------------
# LO-004
# --------------------------------------------------------------------------------------


@register
class TextShapeOverlap(Rule):
    """Reports two text-bearing content shapes whose boxes overlap materially.

    Overlap area comes from :func:`rect_intersection_area_pt2` on the stored
    boxes and is expressed as a share of the *smaller* shape's area, so a caption
    dropped onto a full-width panel is measured against the caption. The rule
    fires above ``profile.layout.overlap_area_share``.

    Three exclusions, each a legitimate overlap rather than a defect: shapes
    without text, so a text box over its own backing panel is not reported, and
    an untexted shape sent to the back of the z-order can never be half of a
    reported pair; furniture, which overlays content by design; and rules,
    dividers and underscores -- anything thinner than :data:`THIN_SHAPE_PT` in
    either dimension -- which underlie text deliberately. Findings are clustered
    per slide, since one dragged box overlapping three columns is one mistake,
    and the worst overlap on the slide is the one named.

    Known false-positive mode: a text box sized generously around short text that
    overlaps a neighbour only in its empty region. Box geometry is what the file
    records; where the glyphs actually land is not.
    """

    id: ClassVar[str] = "LO-004"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "Two text-bearing shapes overlap"
    requires: ClassVar[tuple[str, ...]] = ("layout.overlap_area_share",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        threshold = profile.layout.overlap_area_share
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []

        for slide in deck.slides:
            candidates = [
                shape
                for shape in content_shapes(slide, furniture)
                if shape.has_text and not _is_thin(shape) and shape.area_pt2 > 0
            ]
            overlaps: list[tuple[float, float, ShapeModel, ShapeModel]] = []
            for index, first in enumerate(candidates):
                for second in candidates[index + 1 :]:
                    area = rect_intersection_area_pt2(first.bbox_pt, second.bbox_pt)
                    if area <= 0:
                        continue
                    share = area / min(first.area_pt2, second.area_pt2)
                    if share > threshold:
                        overlaps.append((share, area, first, second))

            # Worst first, so the representative that survives clustering is the
            # overlap a reader would actually notice.
            overlaps.sort(key=lambda item: (-item[0], item[2].ref.shape_id))
            for share, area, first, second in overlaps:
                upper, lower = (
                    (first, second) if first.z_order >= second.z_order else (second, first)
                )
                findings.append(
                    self.finding(
                        where=upper.ref,
                        profile=profile,
                        provenance_path="layout.overlap_area_share",
                        message=(
                            f"{upper.ref.name} overlaps {lower.ref.name} across "
                            f"{share * 100:.0f}% of the smaller shape "
                            f"({area:.0f} square points); both carry text"
                        ),
                        measured=f"{share * 100:.1f}% of the smaller shape",
                        expected=f"at most {threshold * 100:g}%",
                        remedy="Separate the shapes, or confirm the overlap is deliberate",
                        bbox_pt=upper.bbox_pt,
                    )
                )
        return cluster_findings(findings)


# --------------------------------------------------------------------------------------
# LO-005
# --------------------------------------------------------------------------------------

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalised_key(text: str) -> str:
    """Casefold and collapse whitespace, so "Foot note " matches "foot  note"."""
    return _WHITESPACE_RE.sub(" ", text.strip()).casefold()


def _recurring_index(
    entries: list[RecurringElement],
) -> dict[tuple[str, str, str], RecurringElement]:
    """Index recurring entries by identity kind, name and archetype.

    The learning engine qualifies a key as ``name:footnote`` or
    ``placeholder:title``, because a shape called "Title" and a shape occupying
    the title placeholder are different claims.

    The archetype has to be part of the key. A title sits at one position on
    content slides and another on section dividers, so the profile carries
    several entries under ``placeholder:title``; indexing on the name alone kept
    whichever came last and left every other archetype's title silently
    unchecked.

    An entry with no archetypes is indexed under ``""`` and used as the fallback
    for any archetype without a specific entry.
    """
    out: dict[tuple[str, str, str], RecurringElement] = {}
    for entry in entries:
        kind, separator, rest = entry.key.partition(":")
        kinds: tuple[tuple[str, str], ...]
        if separator and kind in ("name", "placeholder"):
            kinds = ((kind, _normalised_key(rest)),)
        else:
            normalised = _normalised_key(entry.key)
            kinds = (("name", normalised), ("placeholder", normalised))
        archetypes = entry.archetypes or [""]
        for identity_kind, name in kinds:
            for archetype in archetypes:
                out.setdefault((identity_kind, name, archetype), entry)
    return out


def _lookup_recurring(
    index: dict[tuple[str, str, str], RecurringElement],
    kind: str,
    name: str,
    archetype: str,
) -> RecurringElement | None:
    """The entry for this archetype, else an archetype-agnostic one."""
    return index.get((kind, _normalised_key(name), archetype)) or index.get(
        (kind, _normalised_key(name), "")
    )


@register
class RecurringElementDisplaced(Rule):
    """Reports a recurring element sitting away from its learned modal position.

    ``profile.layout.recurring`` holds the shapes the reference deck placed
    consistently across slides, each with the modal box it occupied and the
    tolerance derived from the spread of that evidence. A shape is matched to an
    entry by its normalised name -- casefolded, whitespace collapsed -- or by its
    placeholder type, whichever the entry's ``key`` field names, and only on the
    archetypes the entry lists. Left and top are compared against the learned
    box; size is not, because drift in size is BR-002's and BR-008's
    measurement and reporting it twice would be two findings for one cause.

    Known false-positive mode: a slide that deliberately relocates a recurring
    element to clear a full-width exhibit -- a footnote moved up under a short
    chart -- is reported, because the file does not record the reason.
    """

    id: ClassVar[str] = "LO-005"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A recurring element has moved off its learned position"
    requires: ClassVar[tuple[str, ...]] = ("layout.recurring",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        index = _recurring_index(profile.layout.recurring)
        findings: list[Finding] = []

        for slide in deck.slides:
            for shape in slide.leaf_shapes():
                entry = _lookup_recurring(
                    index, "name", shape.ref.name, slide.archetype
                )
                if entry is None and shape.placeholder_type:
                    entry = _lookup_recurring(
                        index, "placeholder", shape.placeholder_type, slide.archetype
                    )
                if entry is None:
                    continue
                drift = _positional_drift(shape, entry.box_pt)
                if drift is None:
                    continue
                axis, measured, expected, distance = drift
                findings.append(
                    self.finding(
                        where=shape.ref,
                        profile=profile,
                        provenance_path="layout.recurring",
                        message=(
                            f"{shape.ref.name} sits {distance:.1f}pt from the position it "
                            f"holds elsewhere in the deck ({entry.box_pt.describe()}, "
                            f"support {entry.support})"
                        ),
                        measured=f"{axis} {measured:.1f}pt",
                        expected=f"{axis} {expected:g}pt",
                        remedy=f"Move it back to {axis} {expected:g}pt",
                        bbox_pt=shape.bbox_pt,
                    )
                )
        return cluster_findings(findings)


def _positional_drift(
    shape: ShapeModel, box: Box
) -> tuple[str, float, float, float] | None:
    """The worse of the two positional drifts, if either exceeds the tolerance."""
    candidates = [
        ("left", shape.left_pt, box.left, abs(shape.left_pt - box.left)),
        ("top", shape.top_pt, box.top, abs(shape.top_pt - box.top)),
    ]
    candidates.sort(key=lambda item: -item[3])
    worst = candidates[0]
    return worst if worst[3] > box.tolerance_pt else None


# --------------------------------------------------------------------------------------
# LO-006
# --------------------------------------------------------------------------------------

#: Metric-compatible substitutions. Liberation Sans is metrically identical to
#: Arial by design and Liberation Serif to Times New Roman, so measuring one and
#: reporting the other is a substitution, not a guess. A typeface neither on this
#: list nor installed under a matching family name is left unmeasured.
_METRIC_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "arial": ("liberationsans",),
    "helvetica": ("liberationsans",),
    "arial narrow": ("liberationsansnarrow",),
    "times new roman": ("liberationserif",),
    "times": ("liberationserif",),
    "courier new": ("liberationmono",),
    "courier": ("liberationmono",),
}

#: Where installed fonts live. Searched in order; the first family match wins.
_FONT_DIRECTORIES: Final[tuple[str, ...]] = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "C:/Windows/Fonts",
)

#: Fonts are loaded once at this pixel size and measurements scaled to the
#: requested point size. Loading at the point size directly would quantise a
#: 7.5pt run to 7px and lose a twentieth of its width.
_MEASURE_PIXELS: Final[int] = 64

#: ``TextParagraph.line_spacing`` carries either a multiple or a point value and
#: does not record which. Anything at or below this is read as a multiple.
_LINE_SPACING_MULTIPLE_CEILING: Final[float] = 5.0


def _font_key(name: str) -> str:
    """A family name reduced to letters and digits, for matching across spellings."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _style_key(*, bold: bool, italic: bool) -> str:
    if bold and italic:
        return "bolditalic"
    if bold:
        return "bold"
    if italic:
        return "italic"
    return "regular"


@functools.lru_cache(maxsize=1)
def _installed_fonts() -> dict[str, dict[str, str]]:
    """Family key -> style key -> font file path, for every installed TTF and OTF.

    Built once per process from each font's *own* family and style names rather
    than its filename, so an "exact family-name match" means the family the deck
    asked for and not a file that happens to be spelled like it. The traversal is
    sorted and the first match per family and style wins, so two machines with
    the same fonts installed produce the same index -- which determinism
    requires.
    """
    index: dict[str, dict[str, str]] = {}
    for directory in _FONT_DIRECTORIES:
        root = Path(directory)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                family, style = ImageFont.truetype(str(path), 16).getname()
            except OSError:
                continue
            if not family:
                continue
            lowered = (style or "Regular").casefold()
            key = _style_key(
                bold="bold" in lowered,
                italic="italic" in lowered or "oblique" in lowered,
            )
            index.setdefault(_font_key(family), {}).setdefault(key, str(path))
    return index


def _resolve_font_path(name: str | None, *, bold: bool, italic: bool) -> str | None:
    """A TTF on this system for ``name``, or None when nothing honest is available.

    Tries the family's own name first, then the metric-compatible alias table.
    Returns None rather than substituting an arbitrary sans-serif: a measurement
    taken in the wrong typeface is worse than no measurement, because it looks
    like a measurement.
    """
    if not name:
        return None
    index = _installed_fonts()
    wanted = _style_key(bold=bold, italic=italic)
    for candidate in (_font_key(name), *_METRIC_ALIASES.get(name.casefold(), ())):
        styles = index.get(candidate)
        if not styles:
            continue
        for style in (wanted, "regular", *sorted(styles)):
            if style in styles:
                return styles[style]
    return None


@functools.lru_cache(maxsize=64)
def _loaded_font(path: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, _MEASURE_PIXELS)


@functools.lru_cache(maxsize=4096)
def _measure(path: str, text: str, size_pt: float) -> tuple[float, float]:
    """``(advance width, ascent + descent)`` in points for ``text`` at ``size_pt``."""
    font = _loaded_font(path)
    scale = size_pt / _MEASURE_PIXELS
    ascent, descent = font.getmetrics()
    return (font.getlength(text) * scale, (ascent + descent) * scale)


@dataclass(frozen=True, slots=True)
class _Token:
    """One word or one run of whitespace, with the metrics of its run."""

    text: str
    width_pt: float
    line_height_pt: float

    @property
    def is_space(self) -> bool:
        return self.text.isspace()


@register
class TextOverflow(Rule):
    """Reports text that does not fit inside the shape holding it.

    Measured, not estimated. Each run's typeface is resolved to a font file
    installed on this machine -- by its own family name, or through a table of
    metric-compatible substitutes whose metrics are identical by design
    (Arial/Liberation Sans, Times New Roman/Liberation Serif) -- and the text is
    laid out with Pillow: greedy word wrap at
    ``width_pt - inset_left_pt - inset_right_pt`` less any paragraph indent, then
    required height as the sum over paragraphs of space before, line count times
    line height taken from the font's own ascent and descent, and space after.
    That total is compared against
    ``height_pt - inset_top_pt - inset_bottom_pt``.

    If any run's typeface cannot be resolved, the whole shape goes into
    ``unchecked`` and nothing is reported: substituting an arbitrary sans-serif
    would produce confident nonsense, and "Gill Sans MT" is installed on no Linux
    box. Tables and charts are likewise recorded unchecked, because their text is
    laid out in cell and plot-area geometry this rule does not model.

    Shrink-on-overflow autofit needs no correction here. The model has already
    folded ``normAutofit/@fontScale`` into the resolved size and marks it in the
    font's provenance, so applying the scale again would shrink the text twice.

    Disabled by default and low confidence, and these are the reasons. Known
    false-positive modes: a shape set to resize itself to its text
    (``autofit == "resize_shape"``), whose stored box is smaller than what
    PowerPoint will render after the next edit; a metric-compatible substitute
    whose real metrics differ from its stand-in; and kerning, hyphenation and
    vertical-anchor subtleties a greedy wrap does not reproduce. Overflow inside
    :data:`OVERFLOW_EPSILON_PT` is not reported, and a shape with wrapping
    switched off is measured for height only -- never for a single line running
    wider than its box, which is a thing designers do on purpose.
    """

    id: ClassVar[str] = "LO-006"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "low"
    default_enabled: ClassVar[bool] = False
    summary: ClassVar[str] = "Text likely overflows its shape"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for slide in deck.slides:
            for shape in slide.leaf_shapes():
                finding = self._check(shape, profile)
                if finding is not None:
                    findings.append(finding)
        return cluster_findings(findings)

    def _check(self, shape: ShapeModel, profile: Profile) -> Finding | None:
        if shape.table is not None or shape.chart is not None:
            self.note_unchecked(
                shape.ref,
                "table and chart text is laid out in geometry this rule does not model",
            )
            return None
        paragraphs = [p for p in shape.text_frame_paragraphs if not p.is_empty]
        if not paragraphs or shape.width_pt <= 0 or shape.height_pt <= 0:
            return None

        available_width = shape.width_pt - shape.inset_left_pt - shape.inset_right_pt
        available_height = shape.height_pt - shape.inset_top_pt - shape.inset_bottom_pt
        if available_width <= 0 or available_height <= 0:
            self.note_unchecked(
                shape.ref, "the insets leave no measurable area inside the shape"
            )
            return None

        # word_wrap is None when inherited, and PowerPoint's inherited default is
        # to wrap; a shape with wrapping off gets one line per paragraph.
        wraps = shape.word_wrap is not False
        required = 0.0
        line_count = 0
        for paragraph in paragraphs:
            tokens = self._tokens(paragraph, shape)
            if tokens is None:
                return None
            indent = max(0.0, paragraph.margin_left_pt or 0.0)
            lines = _wrap(tokens, max(1.0, available_width - indent)) if wraps else [tokens]
            line_count += len(lines)
            required += (
                (paragraph.space_before_pt or 0.0)
                + len(lines) * _line_height(paragraph, tokens)
                + (paragraph.space_after_pt or 0.0)
            )

        overflow = required - available_height
        if overflow <= OVERFLOW_EPSILON_PT:
            return None
        autofit = f", autofit {shape.autofit}" if shape.autofit else ""
        return self.finding(
            where=shape.ref,
            profile=profile,
            provenance_path="layout",
            message=(
                f"{shape.ref.name} needs {line_count} line(s) and {required:.1f}pt of "
                f"height but has {available_height:.1f}pt inside its insets, so the text "
                f"overflows by {overflow:.1f}pt{autofit}"
            ),
            measured=f"{required:.1f}pt of text",
            expected=f"at most {available_height:.1f}pt",
            remedy="Shorten the text, or enlarge the shape",
            bbox_pt=shape.bbox_pt,
        )

    def _tokens(self, paragraph: TextParagraph, shape: ShapeModel) -> list[_Token] | None:
        """Measured words for one paragraph, or None if any run is unmeasurable.

        A single unresolvable run disqualifies the whole shape rather than just
        itself: a height measured from half the paragraphs would be a number with
        no meaning, presented as a measurement.
        """
        tokens: list[_Token] = []
        for run in paragraph.runs:
            if not run.text:
                continue
            font = run.font
            size_pt = font.size_pt
            if size_pt is None or size_pt <= 0:
                self.note_unchecked(
                    shape.ref, "a run has no resolved font size to measure with"
                )
                return None
            path = _resolve_font_path(
                font.name, bold=bool(font.bold), italic=bool(font.italic)
            )
            if path is None:
                self.note_unchecked(
                    shape.ref,
                    f"no font file on this system matches "
                    f"{font.name or 'an unnamed typeface'}, so overflow cannot be "
                    f"measured without guessing",
                )
                return None
            # Whitespace is kept as its own token: it is what the wrap measures
            # against, and it must not be counted at the end of a line.
            for word in _WHITESPACE_SPLIT_RE.split(run.text):
                if not word:
                    continue
                width_pt, line_height_pt = _measure(path, word, size_pt)
                tokens.append(
                    _Token(text=word, width_pt=width_pt, line_height_pt=line_height_pt)
                )
        return tokens


_WHITESPACE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(\s+)")


def _wrap(tokens: Sequence[_Token], width_pt: float) -> list[list[_Token]]:
    """Greedy word wrap. Whitespace never begins a line nor pushes one over."""
    lines: list[list[_Token]] = []
    current: list[_Token] = []
    used = 0.0
    for token in tokens:
        if token.is_space:
            if current:
                current.append(token)
                used += token.width_pt
            continue
        if current and used + token.width_pt > width_pt:
            lines.append(current)
            current = [token]
            used = token.width_pt
            continue
        current.append(token)
        used += token.width_pt
    if current:
        lines.append(current)
    return lines or [[]]


def _line_height(paragraph: TextParagraph, tokens: Sequence[_Token]) -> float:
    """One line's height: the tallest run in the paragraph times its line spacing."""
    tallest = max((token.line_height_pt for token in tokens), default=0.0)
    spacing = paragraph.line_spacing
    if spacing is None or spacing <= 0:
        return tallest
    if spacing <= _LINE_SPACING_MULTIPLE_CEILING:
        return tallest * spacing
    # A value this large came from a:lnSpc/a:spcPts and is already in points.
    return spacing


# --------------------------------------------------------------------------------------
# LO-007
# --------------------------------------------------------------------------------------


@register
class FontSizeOutsideBand(Rule):
    """Reports a resolved font size outside the learned band for its text role.

    The role comes from :func:`font_role`, which is structural -- title
    placeholder, table, chart, footer band, disclaimer body -- rather than
    size-driven, because inferring a role from size and then checking that size
    against a band derived per role would be circular. The band comes from
    ``profile.brand.fonts.roles[role]`` and is tested with
    :meth:`FontRole.permits`, so an exact allowed set and an inclusive range are
    both honoured. A role absent from the profile, or present but empty, is not
    checked -- which is how ``chart_label``, recorded in ``not_learned`` for want
    of evidence, stays silent instead of being measured against a band invented
    from two charts.

    The sizes compared are the rendered ones: the model has already folded
    autofit's font scale into them, and the rendered size is what a reader
    measures.

    Findings are clustered per slide, per role, per offending size, so a body
    placeholder retyped at 18pt throughout is one finding carrying the character
    count rather than one finding per paragraph.

    Known false-positive mode: a deliberate display size -- a single large pull
    quote, one figure called out at 32pt -- is a body run outside the body band
    and is reported. Furniture is excluded, and text in the footer band scores as
    ``footnote``, so the common legitimate exceptions do not reach here.
    """

    id: ClassVar[str] = "LO-007"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "major"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "A font size falls outside the learned band for its role"
    requires: ClassVar[tuple[str, ...]] = ("brand.fonts.roles",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        roles = profile.brand.fonts.roles
        furniture = self.furniture(deck, profile)
        #: (slide, role, size) -> the shapes carrying it and the character count.
        offences: dict[tuple[int, str, float], tuple[list[ShapeRef], int]] = {}
        unbanded: set[tuple[int, str]] = set()

        for slide in deck.slides:
            for shape in content_shapes(slide, furniture):
                role = font_role(slide, shape, furniture=furniture)
                band = roles.get(role)
                if band is None or band.is_empty:
                    if shape.has_text:
                        unbanded.add((slide.index, role))
                    continue
                for paragraph in shape.all_paragraphs:
                    for run in paragraph.runs:
                        size_pt = run.font.size_pt
                        if not run.text.strip() or size_pt is None:
                            continue
                        if band.permits(size_pt):
                            continue
                        key = (slide.index, role, round(size_pt, 2))
                        refs, chars = offences.get(key, ([], 0))
                        if shape.ref not in refs:
                            refs.append(shape.ref)
                        offences[key] = (refs, chars + len(run.text))

        for slide_index, role in sorted(unbanded):
            self.note_unchecked(slide_index, f"no size band was learned for the {role} role")

        findings: list[Finding] = []
        for (_, role, size_pt), (refs, chars) in sorted(offences.items()):
            band = roles[role]
            names = ", ".join(sorted({ref.display_name for ref in refs}))
            findings.append(
                self.finding(
                    where=refs[0],
                    profile=profile,
                    provenance_path=f"brand.fonts.roles.{role}",
                    message=(
                        f"{chars} characters of {role} text are set at {size_pt:g}pt, "
                        f"outside the learned band ({band.describe()}), in {names}"
                    ),
                    measured=f"{size_pt:g}pt",
                    expected=band.describe(),
                    remedy=f"Set the size to {band.describe()}",
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# LO-008
# --------------------------------------------------------------------------------------


#: How far two bullets at the same level may sit apart before it reads as a
#: mistake rather than as rounding. PowerPoint writes indents in EMU, so exact
#: equality is too strict for a deck that has been through a resize.
BULLET_INDENT_TOLERANCE_PT: Final[float] = 1.0

#: Bullets at one level in one list, below which "they disagree" is not a useful
#: thing to say: two bullets at different indents are a two-level list.
MIN_BULLETS_PER_LEVEL: Final[int] = 3


@register
class InconsistentBulletIndent(Rule):
    """Reports one list whose bullets at the same level do not share an indent.

    Measured within a single text frame, not across the deck. A sidebar's bullets
    and a body's bullets are set to different indents on purpose in most house
    styles, and comparing them would report every deck that has a sidebar. Inside
    one list, though, a bullet that sits 4pt right of its siblings is a tab
    somebody pressed, and it is visible to a reader at a glance even though no
    other rule in the tool looks at it.

    Measures ``margin_left_pt`` -- where the bullet's text begins -- per indent
    level, for levels carrying at least three bullets. The level itself is taken
    from the paragraph rather than inferred from the indent, so a properly
    demoted sub-bullet is not read as a misaligned one: this reports bullets that
    *claim* the same level and do not look it.

    Known false-positive mode: a list that deliberately hangs one item further
    in -- a continuation line styled as a bullet, say -- is reported. That is
    rare enough, and visible enough in the message, to be worth the alternative.
    """

    id: ClassVar[str] = "LO-009"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "Bullets at one level of a list do not share an indent"

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []
        for slide in deck.slides:
            for shape in content_shapes(slide, furniture):
                findings.extend(self._in_frame(shape))
        return findings

    def _in_frame(self, shape: ShapeModel) -> list[Finding]:
        by_level: dict[int, list[tuple[float, str]]] = {}
        for paragraph in shape.text_frame_paragraphs:
            if not paragraph.text.strip() or not paragraph.is_bulleted:
                continue
            indent = paragraph.margin_left_pt
            if indent is None:
                # Inherited from the layout, which means the deck has not
                # overridden it and there is nothing here that a person typed.
                continue
            by_level.setdefault(paragraph.level, []).append(
                (indent, paragraph.text.strip())
            )

        findings: list[Finding] = []
        for level, entries in sorted(by_level.items()):
            if len(entries) < MIN_BULLETS_PER_LEVEL:
                continue
            clusters = cluster_values(
                [indent for indent, _ in entries], BULLET_INDENT_TOLERANCE_PT
            )
            if len(clusters) < 2:
                continue
            dominant = max(clusters, key=lambda c: c.support)
            odd = [
                (indent, text)
                for indent, text in entries
                if abs(indent - dominant.mode) > BULLET_INDENT_TOLERANCE_PT
            ]
            if not odd:  # pragma: no cover - implied by the cluster count
                continue
            listed = ", ".join(f"{text[:28]!r} at {indent:g}pt" for indent, text in odd[:3])
            findings.append(
                self.finding(
                    where=shape.ref,
                    message=(
                        f"{len(odd)} of {len(entries)} level-{level + 1} bullets sit at a "
                        f"different indent from the rest ({listed})"
                    ),
                    measured=f"{len(clusters)} indents at level {level + 1}",
                    expected=f"every level-{level + 1} bullet at {dominant.mode:g}pt",
                    remedy=f"Set these bullets to the same {dominant.mode:g}pt indent",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings


@dataclass(frozen=True, slots=True)
class _Axis:
    """One reading direction, so rows and columns share the gutter arithmetic."""

    name: str
    #: The coordinate that must agree for two shapes to sit on the same line.
    cross: Callable[[ShapeModel], float]
    #: The leading and trailing coordinates along the reading direction.
    lead: Callable[[ShapeModel], float]
    trail: Callable[[ShapeModel], float]


_ROW: Final[_Axis] = _Axis(
    name="row",
    cross=lambda shape: shape.top_pt,
    lead=lambda shape: shape.left_pt,
    trail=lambda shape: shape.right_pt,
)
_COLUMN: Final[_Axis] = _Axis(
    name="column",
    cross=lambda shape: shape.left_pt,
    lead=lambda shape: shape.top_pt,
    trail=lambda shape: shape.bottom_pt,
)


@register
class InconsistentGutters(Rule):
    """Reports a detected row or column of sibling shapes with uneven gutters.

    A row is at least :data:`MIN_SIBLINGS` content shapes whose tops cluster
    within ``profile.layout.position_tolerance_pt`` **and** whose widths and
    heights agree within that same tolerance; a column is the same test on left
    edges. The size agreement is what makes them siblings rather than merely
    aligned, and it is load-bearing: without it the title, the rule beneath it
    and the footnote of an entirely ordinary slide share a left edge and read as
    a column with wildly uneven vertical gaps. Gutters are the gaps between
    consecutive trailing and leading edges, and the rule fires when their
    standard deviation exceeds ``profile.layout.gutter_stdev_pt``. One finding
    per detected group, because a four-card row with one card nudged has one
    cause.

    Known false-positive mode: a row of equal-sized cards deliberately grouped
    two-and-two, with a wider gap in the middle to separate the pairs, has
    genuinely uneven gutters and is reported.
    """

    id: ClassVar[str] = "LO-008"
    category: ClassVar[str] = "layout"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "high"
    summary: ClassVar[str] = "Sibling shapes in a row or column have uneven gutters"
    requires: ClassVar[tuple[str, ...]] = (
        "layout.gutter_stdev_pt",
        "layout.position_tolerance_pt",
    )

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        tolerance = profile.layout.position_tolerance_pt
        limit = profile.layout.gutter_stdev_pt
        furniture = self.furniture(deck, profile)
        findings: list[Finding] = []

        for slide in deck.slides:
            shapes = content_shapes(slide, furniture)
            for axis in (_ROW, _COLUMN):
                for group in _sibling_groups(shapes, axis, tolerance):
                    units = _touching_runs(group, axis, tolerance)
                    if len(units) < MIN_SIBLINGS:
                        continue
                    gutters = [
                        axis.lead(later[0]) - axis.trail(earlier[-1])
                        for earlier, later in itertools.pairwise(units)
                    ]
                    # A negative gutter means consecutive members overlap, so
                    # they are stacked rather than set out in a row: a KPI card
                    # and the text box drawn over it agree in position and size
                    # and so pass the sibling test, then produce gutters like
                    # (-20.2pt, 427.2pt, -20.2pt). Reporting that as uneven
                    # spacing is nonsense on its face, and the overlap itself is
                    # LO-004's to report. Measured against the same tolerance
                    # that decided they were siblings, so shapes merely touching
                    # still count as a row.
                    if min(gutters) < -tolerance:
                        continue
                    spread = statistics.stdev(gutters)
                    if spread <= limit:
                        continue
                    findings.append(
                        self.finding(
                            where=group[0].ref,
                            profile=profile,
                            provenance_path="layout.gutter_stdev_pt",
                            message=(
                                f"gutters down a {len(units)}-item {axis.name} vary by "
                                f"{spread:.1f}pt "
                                # `+ 0.0` so a gutter of exactly zero measured from
                                # the negative side does not print as "-0.0pt".
                                f"({', '.join(f'{gutter + 0.0:.1f}pt' for gutter in gutters)}) "
                                f"in {', '.join(shape.ref.name for shape in group)}"
                            ),
                            measured=f"stdev {spread:.2f}pt",
                            expected=f"stdev at most {limit:g}pt",
                            remedy=f"Space the items evenly down the {axis.name}"
                            if axis is _COLUMN
                            else "Space the items evenly across the row",
                        )
                    )
        return findings


def _touching_runs(
    group: Sequence[ShapeModel], axis: _Axis, tolerance: float
) -> list[list[ShapeModel]]:
    """Consecutive shapes that touch, collapsed into the units a reader sees.

    A timetable entry is a heading with its caption sitting directly beneath it,
    no gap at all. Four such entries are eight shapes of equal width on one
    column, which is exactly the sibling test, and their gutters run
    ``0, 26.6, 0, 26.6, 0, 26.6, 0`` -- a standard deviation of 14pt and a
    finding that reads "gutters vary by 14.2pt" about a layout whose four items
    are evenly spaced to the point.

    Two shapes flush against each other are one thing on the slide, not two
    badly spaced ones, so they are measured as one. What is left is the spacing a
    reader would actually call uneven.
    """
    if not group:
        return []
    units: list[list[ShapeModel]] = [[group[0]]]
    for earlier, later in itertools.pairwise(group):
        if axis.lead(later) - axis.trail(earlier) <= tolerance:
            units[-1].append(later)
        else:
            units.append([later])
    return units


def _sibling_groups(
    shapes: Sequence[ShapeModel], axis: _Axis, tolerance: float
) -> list[list[ShapeModel]]:
    """Groups of same-sized shapes sharing a line, ordered along the axis.

    Deduplicated by shape-id tuple, because every member of a group would
    otherwise nominate that same group as its own anchor.
    """
    if len(shapes) < MIN_SIBLINGS:
        return []
    groups: list[list[ShapeModel]] = []
    seen: set[tuple[int, ...]] = set()
    for cluster in cluster_values([axis.cross(shape) for shape in shapes], tolerance):
        on_line = [
            shape for shape in shapes if abs(axis.cross(shape) - cluster.centre) <= tolerance
        ]
        for anchor in on_line:
            siblings = sorted(
                (
                    shape
                    for shape in on_line
                    if abs(shape.width_pt - anchor.width_pt) <= tolerance
                    and abs(shape.height_pt - anchor.height_pt) <= tolerance
                ),
                key=axis.lead,
            )
            if len(siblings) < MIN_SIBLINGS:
                continue
            key = tuple(shape.ref.shape_id for shape in siblings)
            if key in seen:
                continue
            seen.add(key)
            groups.append(siblings)
    return groups
