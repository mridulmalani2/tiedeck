"""Deriving the layout profile: slide size, safe margins, grid and recurring elements.

The grid is the most valuable thing in here, and the least obvious. Clustering
every shape edge in the deck and keeping the clusters with real support turns a
vague instruction ("things should line up") into a testable one ("the left edge
of a content block is at 36, 264, 492 or 720pt"). That in turn is what makes
near-miss alignment checking worth having: without a grid, a shape 3pt from its
neighbour is indistinguishable from a deliberate offset, and the rule either
reports everything or nothing.

One deliberate addition to section 8.3's margin recipe is documented at
:func:`derive_margins`.
"""

from __future__ import annotations

import itertools
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from tieout.cluster import Cluster, cluster_values, derive_tolerance, floor_to, percentile
from tieout.learn.classify import (
    Classification,
    Derivation,
    ObservationClass,
    classify_numeric,
)
from tieout.learn.observe import (
    DECK,
    Observation,
    archetype_scope,
    archetypes_present,
    learnable_slides,
)
from tieout.model.archetype import CONTENT_ARCHETYPES, SPARSE_ARCHETYPES
from tieout.model.deck import DeckModel, ShapeModel, SlideModel
from tieout.model.furniture import Furniture, content_shapes
from tieout.profile.schema import (
    Box,
    GridProfile,
    LayoutProfile,
    Margins,
    NearMissWindow,
    RecurringElement,
)

#: Margins are reported to this granularity, rounded down.
MARGIN_UNIT_PT: Final[float] = 2.0

#: Percentile of observed edge positions taken as the margin, per section 8.3.
MARGIN_PERCENTILE: Final[float] = 0.05

#: A shape covering at least this share of the canvas is full bleed and excluded.
FULL_BLEED_SHARE: Final[float] = 0.80

#: An edge cluster needs this much support to be a grid line, per section 8.3.
GRID_MIN_SUPPORT: Final[int] = 5

#: Slides an edge cluster must span before it is a grid line rather than the
#: outline of one component.
#:
#: Shape count alone was the whole test, and six cards in a row on one slide
#: clear it twice over -- once for their shared top edge, once for their shared
#: bottom. Both became deck-wide grid lines, and shapes on other slides with
#: nothing to do with those cards were then reported for sitting 2pt off them.
#: A grid is a structure the deck returns to; evidence from a single slide is a
#: design element, and every other convention here already has to recur across
#: slides to count as one.
GRID_MIN_SLIDES: Final[int] = 2

#: Edge clustering tolerance, per section 8.2.
GRID_TOLERANCE_PT: Final[float] = 2.0

#: A recurring element must appear on at least this many slides.
RECURRING_MIN_SUPPORT: Final[int] = 3


@dataclass
class LayoutDerivation:
    profile: LayoutProfile
    derivation: Derivation = field(default_factory=Derivation)


def derive_layout(deck: DeckModel, furniture: Furniture) -> LayoutDerivation:
    """Derive the whole layout profile."""
    result = LayoutDerivation(profile=LayoutProfile())
    total_slides = deck.slide_count

    result.profile.safe_margin_pt = derive_margins(deck, furniture, result, total_slides)
    result.profile.grid = derive_grid(deck, furniture, result, total_slides)
    result.profile.recurring = derive_recurring(deck, furniture, result, total_slides)
    result.profile.near_miss_alignment_pt = NearMissWindow()
    result.derivation.note(
        "layout.near_miss_alignment_pt",
        "default window: below 0.5pt is indistinguishable from aligned and above "
        "4pt is plainly a different position rather than a mistake. Not inferred",
        "medium",
    )
    result.profile.position_tolerance_pt = GRID_TOLERANCE_PT
    return result


# --------------------------------------------------------------------------------------
# Margins
# --------------------------------------------------------------------------------------


def derive_margins(
    deck: DeckModel,
    furniture: Furniture,
    result: LayoutDerivation,
    total_slides: int,
) -> dict[str, Margins]:
    """Safe margins per archetype, from the distribution of content edges.

    Section 8.3 prescribes the 5th percentile of content bounding box edges,
    excluding full-bleed shapes, the logo and the footer, rounded down to the
    nearest 2pt.

    One addition: the result is also clamped so it can never exceed the
    *tightest* edge actually observed. The percentile exists to tolerate a few
    shapes that legitimately sit outside the content frame, but every shape it
    tolerates is a shape LO-002 would then report -- on the very deck the margin
    was learned from. Since the exclusions above already remove the legitimate
    outliers, the clamp costs nothing and it guarantees the profile cannot fail
    its own reference deck. Recorded in the provenance whenever it binds.

    Margins are derived only for archetypes that carry content. A section
    divider's single centred line has no meaningful margin, and deriving one from
    it would produce a rule that fires on the next divider whose heading is a
    different length.
    """
    out: dict[str, Margins] = {}
    for archetype in archetypes_present(deck):
        path = f"layout.safe_margin_pt.{archetype}"
        if archetype in SPARSE_ARCHETYPES:
            result.derivation.unlearned(
                path,
                f"{archetype} slides carry too little content for a margin to be "
                f"meaningful",
            )
            continue

        slides = [s for s in learnable_slides(deck) if s.archetype == archetype]
        edges: dict[str, list[float]] = {"top": [], "right": [], "bottom": [], "left": []}
        for slide in slides:
            for shape in content_shapes(slide, furniture):
                if shape.area_pt2 / (slide.area_pt2 or 1.0) >= FULL_BLEED_SHARE:
                    continue
                box = shape.visual_bbox_pt
                edges["left"].append(box[0])
                edges["top"].append(box[1])
                edges["right"].append(slide.width_pt - (box[0] + box[2]))
                edges["bottom"].append(slide.height_pt - (box[1] + box[3]))

        if min(len(v) for v in edges.values()) < 2:
            result.derivation.unlearned(
                path,
                f"only {min(len(v) for v in edges.values())} content edge "
                f"observations on {len(slides)} {archetype} slide"
                f"{'s' if len(slides) != 1 else ''}",
            )
            continue

        values: dict[str, float] = {}
        clamped: list[str] = []
        for name, observations in edges.items():
            raw = percentile(observations, MARGIN_PERCENTILE) or 0.0
            tightest = min(observations)
            if raw > tightest:
                clamped.append(name)
            values[name] = max(0.0, floor_to(min(raw, tightest), MARGIN_UNIT_PT))

        out[archetype] = Margins(
            top=values["top"],
            right=values["right"],
            bottom=values["bottom"],
            left=values["left"],
        )
        note = (
            f"5th percentile of {len(edges['left'])} content edge observations "
            f"across {len(slides)} {archetype} slide"
            f"{'s' if len(slides) != 1 else ''}, excluding the logo, the footer "
            f"and full-bleed shapes, rounded down to {MARGIN_UNIT_PT:g}pt"
        )
        if clamped:
            note += (
                f"; the {', '.join(sorted(clamped))} margin"
                f"{'s were' if len(clamped) > 1 else ' was'} clamped to the "
                f"tightest edge observed so the rule cannot fail this deck"
            )
        result.derivation.note(path, note, "high")

    if not out:
        result.derivation.unlearned(
            "layout.safe_margin_pt",
            "no archetype in the reference deck carries enough content to derive "
            "a margin",
        )
    del total_slides
    return out


# --------------------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------------------


def derive_grid(
    deck: DeckModel,
    furniture: Furniture,
    result: LayoutDerivation,
    total_slides: int,
) -> GridProfile:
    """Cluster shape edges deck-wide; clusters with real support are grid lines.

    Both left and right edges feed the column grid, and both top and bottom feed
    the row grid, because a designer aligns to whichever edge is nearer the
    content. Furniture is excluded: the logo and page number sit at positions
    nothing else aligns to, and admitting them would create grid lines that exist
    only to catch the chrome itself.
    """
    columns: list[float] = []
    rows: list[float] = []
    # Which slides each edge position came from, so a cluster can be asked
    # whether it is a deck-wide structure or one slide's furniture.
    column_slides: dict[float, set[int]] = {}
    row_slides: dict[float, set[int]] = {}
    for slide in learnable_slides(deck):
        for shape in content_shapes(slide, furniture):
            box = shape.visual_bbox_pt
            for value in (box[0], box[0] + box[2]):
                columns.append(value)
                column_slides.setdefault(value, set()).add(slide.index)
            for value in (box[1], box[1] + box[3]):
                rows.append(value)
                row_slides.setdefault(value, set()).add(slide.index)

    grid = GridProfile(tolerance_pt=GRID_TOLERANCE_PT)
    for values, by_slide, attribute, label in (
        (columns, column_slides, "columns_pt", "vertical"),
        (rows, row_slides, "rows_pt", "horizontal"),
    ):

        def _slides(cluster: Cluster, by_slide: dict[float, set[int]] = by_slide) -> int:
            covered: set[int] = set()
            for member in cluster.members:
                covered |= by_slide.get(member, set())
            return len(covered)

        clusters = [
            cluster
            for cluster in cluster_values(values, GRID_TOLERANCE_PT)
            if cluster.support >= GRID_MIN_SUPPORT and _slides(cluster) >= GRID_MIN_SLIDES
        ]
        lines = sorted(round(cluster.mode, 2) for cluster in clusters)
        setattr(grid, attribute, lines)
        path = f"layout.grid.{attribute}"
        if lines:
            supports = {
                round(cluster.mode, 2): cluster.support for cluster in clusters
            }
            result.derivation.note(
                path,
                f"{len(lines)} {label} edge clusters, each on {GRID_MIN_SUPPORT} or "
                f"more shapes across at least {GRID_MIN_SLIDES} of {total_slides} "
                "slides (support: "
                + ", ".join(f"{line:g}pt on {supports[line]}" for line in lines)
                + ")",
                "high",
            )
        else:
            result.derivation.unlearned(
                path,
                f"no {label} edge position recurs on {GRID_MIN_SUPPORT} or more "
                f"shapes across {GRID_MIN_SLIDES} or more slides, so there is no "
                f"grid to align to",
            )
    return grid


# --------------------------------------------------------------------------------------
# Recurring elements
# --------------------------------------------------------------------------------------


def _recurring_key(shape: ShapeModel) -> str | None:
    """Identity by which a shape is matched across slides.

    Placeholder type first, because it is the designer's own label and survives
    renaming. Otherwise the normalised shape name, which in practice is what a
    template's furniture carries. A shape with a PowerPoint default name such as
    "TextBox 4" is not matched: those numbers are assigned in creation order and
    mean nothing across slides.
    """
    if shape.placeholder_type:
        return f"placeholder:{shape.placeholder_type}"
    name = " ".join(shape.ref.name.split()).casefold()
    if not name or name == "<unnamed>":
        return None
    generic = ("textbox", "rectangle", "picture", "shape", "oval", "freeform", "group")
    stem = name.rsplit(" ", 1)[0] if name.rsplit(" ", 1)[-1].isdigit() else name
    if stem in generic:
        return None
    return f"name:{stem}"


def derive_recurring(
    deck: DeckModel,
    furniture: Furniture,
    result: LayoutDerivation,
    total_slides: int,
) -> list[RecurringElement]:
    """Elements that recur across slides at a consistent position.

    Derived deck-wide first. When a key turns out to be multimodal -- a title
    sits high on content slides and centred on dividers -- the archetype
    machinery is used to explain it, and one entry is emitted per archetype that
    resolves. This is section 8.2's "attempt to explain by archetype" applied to
    geometry, and it is the difference between "the title has two positions, no
    rule" and two usable rules.
    """
    observations: dict[str, list[tuple[ShapeModel, SlideModel]]] = {}
    for slide in learnable_slides(deck):
        for shape in slide.leaf_shapes():
            if furniture.is_furniture(slide.index, shape.ref.shape_id):
                continue
            key = _recurring_key(shape)
            if key is None:
                continue
            observations.setdefault(key, []).append((shape, slide))

    out: list[RecurringElement] = []
    for key, entries in sorted(observations.items()):
        if len(entries) < RECURRING_MIN_SUPPORT:
            continue
        path = f"layout.recurring.{key}"

        deck_wide = _classify_box(key, DECK, entries)
        if deck_wide is not None:
            box, worst = deck_wide
            out.append(
                RecurringElement(
                    key=key,
                    box_pt=box,
                    archetypes=sorted({slide.archetype for _, slide in entries}),
                    support=len(entries),
                )
            )
            result.derivation.note(
                path,
                f"{key} observed on {len(entries)} shapes across "
                f"{len({s.index for _, s in entries})} of {total_slides} slides, "
                f"{worst.observation_class.value} at a modal position with a "
                f"{box.tolerance_pt:g}pt tolerance",
                worst.confidence,
            )
            continue

        explained = 0
        for archetype in sorted({slide.archetype for _, slide in entries}):
            subset = [e for e in entries if e[1].archetype == archetype]
            if len(subset) < 2:
                continue
            per_archetype = _classify_box(key, archetype_scope(archetype), subset)
            if per_archetype is None:
                continue
            box, worst = per_archetype
            out.append(
                RecurringElement(
                    key=key,
                    box_pt=box,
                    archetypes=[archetype],
                    support=len(subset),
                )
            )
            result.derivation.note(
                f"{path}.{archetype}",
                f"{key} has no single deck-wide position, but is "
                f"{worst.observation_class.value} across the {len(subset)} "
                f"{archetype} slides that carry it",
                "medium",
            )
            explained += 1

        if not explained:
            result.derivation.unlearned(
                path,
                f"{key} appears on {len(entries)} shapes but at no consistent "
                f"position, deck-wide or within any archetype",
            )
    return out


def _classify_box(
    key: str, scope: str, entries: list[tuple[ShapeModel, SlideModel]]
) -> tuple[Box, Classification] | None:
    """Classify a group of shapes' geometry into one expected box, or None."""
    edges: dict[str, Classification] = {}
    getters: tuple[tuple[str, Callable[[ShapeModel], float]], ...] = (
        ("left", lambda s: s.left_pt),
        ("top", lambda s: s.top_pt),
        ("width", lambda s: s.width_pt),
        ("height", lambda s: s.height_pt),
    )
    for name, getter in getters:
        edges[name] = classify_numeric(
            f"{key}.{name}",
            scope,
            [
                Observation(f"{key}.{name}", scope, getter(shape), slide.index)
                for shape, slide in entries
            ],
            tolerance=GRID_TOLERANCE_PT,
        )

    if any(
        c.observation_class
        not in (ObservationClass.INVARIANT, ObservationClass.DOMINANT)
        for c in edges.values()
    ):
        return None

    spread = max(c.spread for c in edges.values())
    worst = min(edges.values(), key=lambda c: c.top_share)
    return (
        Box(
            left=round(float(edges["left"].value), 2),
            top=round(float(edges["top"].value), 2),
            width=round(float(edges["width"].value), 2),
            height=round(float(edges["height"].value), 2),
            tolerance_pt=derive_tolerance(spread, floor=2.0),
        ),
        worst,
    )


# --------------------------------------------------------------------------------------
# Gutters
# --------------------------------------------------------------------------------------


def derive_gutter(
    deck: DeckModel, furniture: Furniture
) -> tuple[float | None, str]:
    """The deck's habitual gutter between side-by-side content blocks.

    Returned separately from the profile because in practice it is frequently
    *not* learnable -- a deck that mixes two, three and four column layouts has
    three different gutters by design -- and section 8.5's own worked example
    records exactly that in ``not_learned``. LO-008 checks gutter *consistency
    within a row* rather than against a deck-wide value, so nothing depends on
    this succeeding.
    """
    gutters: list[float] = []
    for slide in learnable_slides(deck):
        if slide.archetype not in CONTENT_ARCHETYPES:
            continue
        shapes = sorted(content_shapes(slide, furniture), key=lambda s: s.left_pt)
        for row in _rows_of(shapes):
            ordered = sorted(row, key=lambda s: s.left_pt)
            for left, right in itertools.pairwise(ordered):
                gap = right.left_pt - (left.left_pt + left.width_pt)
                if gap > 0:
                    gutters.append(round(gap, 2))

    if len(gutters) < 3:
        return None, f"only {len(gutters)} gutter observations, too few to derive from"

    clusters = cluster_values(gutters, GRID_TOLERANCE_PT)
    total = sum(c.weight for c in clusters)
    top = clusters[0]
    if top.weight / total < 0.85:
        spread = f"{min(gutters):g}pt to {max(gutters):g}pt"
        return None, f"no consistent gutter found, spread {spread}"
    return top.mode, (
        f"dominant on {top.weight / total:.0%} of {len(gutters)} observed gaps "
        f"between side-by-side content blocks"
    )


def _rows_of(shapes: list[ShapeModel]) -> list[list[ShapeModel]]:
    """Group shapes whose tops coincide into rows of three or more."""
    if not shapes:
        return []
    tops = [s.top_pt for s in shapes]
    rows: list[list[ShapeModel]] = []
    for cluster in cluster_values(tops, GRID_TOLERANCE_PT):
        members = [
            s
            for s in shapes
            if any(abs(s.top_pt - m) < 1e-9 for m in cluster.members)
        ]
        if len(members) >= 3:
            rows.append(members)
    return rows


def gutter_stdev(values: list[float]) -> float:
    """Standard deviation helper, so LO-008 and this module agree."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)
