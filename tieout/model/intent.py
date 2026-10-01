"""Evidence that a shape's position was chosen, as a field rather than a special case.

PLAN.md §0. A layout rule measures where a shape is against where the house
style says shapes go, and reports the difference. What it could not say was
whether anything in the deck suggests the difference was meant -- so a cover
device bled off the edge on purpose and a takeaway box set in from its column
on every slide were reported exactly as a shape dragged by accident would be.

The evidence existed, in fragments, each bolted onto the rule that needed it:
furniture detection, the structural decorative bleed, a slide's own alignment
lines, evenly spaced runs, shapes whose position plots a value. This module
gives them one shape -- :class:`Evidence` on a :class:`Placement` -- so every
rule reads the same answer and a silence can say which evidence it rested on.
The fragments themselves are not re-derived here; they stay where they were
built and are gathered by :func:`tieout.rules.layout.placement_intent`. What is
new here is the one piece of evidence nothing read before: **the same element
placed identically on several slides.**

Air-gapped, like everything in :mod:`tieout`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from tieout.model.deck import ShapeModel

__all__ = [
    "REPEATED_ON_SLIDES",
    "REPEAT_TOLERANCE_PT",
    "Evidence",
    "EvidenceKind",
    "Placement",
    "repeated_placements",
]

#: Why a position reads as chosen.
#:
#: * ``furniture`` -- the logo, page number or boilerplate the deck repeats.
#: * ``decorative`` -- untexted, behind the content, partly off the canvas.
#: * ``aligned`` -- an edge on the learned grid, or on a line the slide's own
#:   shapes share.
#: * ``spaced`` -- a member of an evenly spaced run, placed by its pitch.
#: * ``data_mark`` -- its position plots a value: a bar, a dot.
#: * ``repeated`` -- the same element, placed identically, on several slides.
#: * ``sparse_slide`` -- on a title or divider slide, where decoration lives.
#: * ``declared`` -- the person said so, and the profile remembers.
EvidenceKind = Literal[
    "furniture",
    "decorative",
    "aligned",
    "spaced",
    "data_mark",
    "repeated",
    "sparse_slide",
    "declared",
]

#: How nearly two placements must agree to be one placement repeated.
#:
#: Much tighter than a grid tolerance, on purpose. A copy-pasted box or a
#: layout placeholder lands on the same EMU every time; a hand nudge does not
#: repeat itself to a quarter of a point. At the grid's 2pt a body box nudged to
#: 493pt on one slide joined a takeaway box set at 493.5pt on three others, and
#: the one real mistake on the deck was excused by its neighbours.
REPEAT_TOLERANCE_PT: Final[float] = 0.25

#: Slides a placement must recur on to be evidence, by what is repeated.
#:
#: A whole shape -- same size, same place -- on two slides is already a
#: decision: a cover device on the cover and on a divider. One axis of a shape
#: (its left and right, say) is weaker, since two boxes of one width in one
#: column are ordinary, so it needs three -- the support
#: :data:`tieout.learn.derive_layout.RECURRING_MIN_SUPPORT` asks of a recurring
#: element, and for the same reason.
REPEATED_ON_SLIDES: Final[Mapping[str, int]] = {"shape": 2, "axis": 3}


@dataclass(frozen=True, slots=True)
class Evidence:
    """One reason to read a shape's position as chosen."""

    kind: EvidenceKind
    #: The axes this evidence accounts for: ``x``, ``y`` or both. A bar's
    #: length is a value on ``x`` and says nothing about its ``y``.
    axes: frozenset[str]
    #: A sentence for the record a rule writes when it stays silent on this.
    detail: str


_BOTH: Final[frozenset[str]] = frozenset({"x", "y"})


@dataclass(frozen=True, slots=True)
class Placement:
    """Everything the deck says about why one shape is where it is."""

    evidence: tuple[Evidence, ...] = ()

    def of_kind(self, *kinds: EvidenceKind) -> Evidence | None:
        return next((e for e in self.evidence if e.kind in kinds), None)

    def explains(self, axis: str, *kinds: EvidenceKind) -> Evidence | None:
        """The first evidence of these kinds accounting for ``axis``."""
        return next(
            (e for e in self.evidence if axis in e.axes and (not kinds or e.kind in kinds)),
            None,
        )

    def with_(self, evidence: Evidence) -> Placement:
        return Placement((*self.evidence, evidence))


def _agrees(one: ShapeModel, other: ShapeModel, attrs: Sequence[str]) -> bool:
    return all(
        abs(getattr(one, attr) - getattr(other, attr)) <= REPEAT_TOLERANCE_PT
        for attr in attrs
    )


_AXIS_ATTRS: Final[Mapping[str, tuple[str, str]]] = {
    "x": ("left_pt", "right_pt"),
    "y": ("top_pt", "bottom_pt"),
}


def repeated_placements(
    shapes_by_slide: Mapping[int, Sequence[ShapeModel]],
) -> dict[tuple[int, int], Evidence]:
    """``(slide, uid)`` -> evidence that this shape's placement recurs.

    Two readings, strongest first. A shape whose whole box recurs on
    ``REPEATED_ON_SLIDES["shape"]`` slides is placed on both axes; otherwise a
    shape whose extent on one axis -- left *and* right, or top *and* bottom --
    recurs on ``REPEATED_ON_SLIDES["axis"]`` slides is placed on that axis.

    Like is compared with like only in the sense that matters: two shapes of a
    different kind or carrying text where the other does not are still the same
    *placement*, because a designer reusing a position is the evidence, not the
    shape that happens to fill it.
    """
    flat = [
        (slide, shape)
        for slide, shapes in shapes_by_slide.items()
        for shape in shapes
        if shape.width_pt > 0 and shape.height_pt > 0
    ]
    out: dict[tuple[int, int], Evidence] = {}
    for slide, shape in flat:
        whole = sorted(
            {
                other_slide
                for other_slide, other in flat
                if _agrees(shape, other, (*_AXIS_ATTRS["x"], *_AXIS_ATTRS["y"]))
            }
        )
        if len(whole) >= REPEATED_ON_SLIDES["shape"]:
            out[(slide, shape.ref.uid)] = Evidence(
                "repeated",
                _BOTH,
                f"the same box, at the same place, is on slides {_list(whole)}",
            )
            continue
        axes: set[str] = set()
        slides_seen: set[int] = set()
        for axis, attrs in _AXIS_ATTRS.items():
            recurring = {
                other_slide for other_slide, other in flat if _agrees(shape, other, attrs)
            }
            if len(recurring) >= REPEATED_ON_SLIDES["axis"]:
                axes.add(axis)
                slides_seen |= recurring
        if axes:
            out[(slide, shape.ref.uid)] = Evidence(
                "repeated",
                frozenset(axes),
                f"the same {' and '.join(_EXTENT[a] for a in sorted(axes))} recurs "
                f"on slides {_list(sorted(slides_seen))}",
            )
    return out


_EXTENT: Final[Mapping[str, str]] = {
    "x": "left and right edges",
    "y": "top and bottom edges",
}


def _list(slides: Sequence[int]) -> str:
    return ", ".join(str(index) for index in slides)
