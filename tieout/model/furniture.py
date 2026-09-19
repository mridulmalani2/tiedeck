"""Identifying deck chrome, and inferring what each piece of text is for.

Two questions that both the learning engine and the rules need answered, and
which must be answered the same way in both or the tool disagrees with itself:

1. Which shapes are *furniture* -- the logo, the page number, the confidentiality
   line? Furniture has to be excluded from margin derivation, from grid
   derivation, from overlap checking and from font-role bands, because it
   deliberately sits outside the content frame. A margin deriver that counts the
   logo learns a top margin of 24pt and then reports every title on the deck.

2. What *role* does a piece of text play -- title, subtitle, body, table cell,
   chart label, footnote? Size bands are per role, and a role assignment that
   lumps 7pt legal type in with 11pt body text produces a band so wide it
   permits anything.

Furniture detection runs in two modes. Given a profile it uses the learned logo
hashes, page-number pattern and boilerplate strings. Without one -- which is the
situation during learning -- it derives them from repetition across the deck,
using the same thresholds the derivers do.

Lives in the model layer rather than under ``learn/`` or ``rules/`` because both
consume it and neither should depend on the other.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from tieout.model.deck import DeckModel, ShapeModel, ShapeRef, SlideModel

if TYPE_CHECKING:
    from tieout.profile.schema import Profile

#: An image on at least this share of slides is a logo candidate (section 8.3).
LOGO_SUPPORT_SHARE: Final[float] = 0.40

#: How close an image must sit to the established logo slot -- in position and
#: in size -- to be read as the same mark in another colourway.
LOGO_VARIANT_TOLERANCE_PT: Final[float] = 2.0

#: How far a backing plate may fall short of enclosing the text set on it. A
#: badge is positioned by eye against its glyph, not snapped to the text box.
LOCKUP_PLATE_TOLERANCE_PT: Final[float] = 2.0

#: How much larger in area than that text a backing plate may be. This bounds the
#: claim to a badge drawn around its own glyph: a panel big enough to be the
#: slide's background is content, whatever text happens to sit on it.
LOCKUP_PLATE_AREA_RATIO: Final[float] = 4.0

#: How far apart two pieces of one lockup may sit, as a multiple of the taller
#: one's height. Scale-relative rather than absolute, because the same mark is
#: set at 19pt in a slide corner and at 36pt on a cover.
LOCKUP_GAP_HEIGHTS: Final[float] = 1.0

#: A string repeated verbatim on at least this share of slides is boilerplate.
BOILERPLATE_SUPPORT_SHARE: Final[float] = 0.60

#: The band at the foot of the slide where footer furniture lives.
FOOTER_BAND_SHARE: Final[float] = 0.12

#: Patterns that identify a confidentiality marking rather than a generic footer.
CONFIDENTIALITY_PATTERNS: Final[tuple[str, ...]] = (
    r"confidential",
    r"private",
    r"internal use",
    r"not for distribution",
    r"do not distribute",
    r"draft",
    r"privileged",
)

_CONFIDENTIALITY_RE: Final[re.Pattern[str]] = re.compile(
    "|".join(CONFIDENTIALITY_PATTERNS), re.IGNORECASE
)

#: A page-number shape's text, in the shapes seen in practice.
_PAGE_NUMBER_CANDIDATES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^\s*(\d{1,3})\s*$"),
    re.compile(r"^\s*[Pp]age\s+(\d{1,3})\s*$"),
    re.compile(r"^\s*(\d{1,3})\s*[|/]\s*\d{1,3}\s*$"),
    re.compile(r"^\s*[Pp]age\s+(\d{1,3})\s+of\s+\d{1,3}\s*$"),
)

#: Shape-name fragments that identify a text role outright.
#:
#: A template names its furniture, and that name is better evidence of intent
#: than the shape's current position. Position alone makes the role change when
#: the shape moves: a footnote dragged up the slide stops being read as a
#: footnote and its 7pt type is then reported as undersized body text, which is
#: a confusing second finding about a defect already reported.
_NAME_ROLE_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("footnote", "footnote"),
    ("foot note", "footnote"),
    ("source", "footnote"),
    ("disclaimer", "footnote"),
    ("legal", "footnote"),
    ("page number", "footnote"),
    ("subtitle", "subtitle"),
    ("sub-title", "subtitle"),
    ("kicker", "subtitle"),
    ("eyebrow", "subtitle"),
)

#: Text roles. ``footnote`` covers source lines, legal type and footer furniture,
#: which all share one size in every house style encountered.
ROLES: Final[tuple[str, ...]] = (
    "title",
    "subtitle",
    "body",
    "table",
    "chart_label",
    "footnote",
)


@dataclass(frozen=True, slots=True)
class SlideFurniture:
    """Which shapes on one slide are chrome rather than content."""

    logo_uids: frozenset[int] = frozenset()
    page_number_uids: frozenset[int] = frozenset()
    boilerplate_uids: frozenset[int] = frozenset()
    #: Drawn shapes carrying no text of their own that back a piece of the above
    #: -- the badge under a monogram, the tab behind a page number.
    lockup_uids: frozenset[int] = frozenset()

    @property
    def all_ids(self) -> frozenset[int]:
        return (
            self.logo_uids
            | self.page_number_uids
            | self.boilerplate_uids
            | self.lockup_uids
        )

    def contains(self, uid: int) -> bool:
        return uid in self.all_ids


@dataclass(frozen=True, slots=True)
class PageNumberObservation:
    """One page number found on a slide, with the value it displayed."""

    slide_index: int
    uid: int
    value: int
    text: str
    box_pt: tuple[float, float, float, float]


@dataclass
class Furniture:
    """Deck-wide furniture identification."""

    by_slide: dict[int, SlideFurniture] = field(default_factory=dict)
    #: SHA1s judged to be logos, ordered by descending slide support.
    logo_sha1s: tuple[str, ...] = ()
    logo_support: dict[str, int] = field(default_factory=dict)
    page_numbers: tuple[PageNumberObservation, ...] = ()
    #: Normalised boilerplate text -> the slide indices carrying it.
    boilerplate: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def for_slide(self, slide_index: int) -> SlideFurniture:
        return self.by_slide.get(slide_index, SlideFurniture())

    def is_furniture(self, slide_index: int, uid: int) -> bool:
        return self.for_slide(slide_index).contains(uid)

    def is_logo(self, slide_index: int, uid: int) -> bool:
        return uid in self.for_slide(slide_index).logo_uids

    def logo_shapes(self, slide: SlideModel) -> list[ShapeModel]:
        ids = self.for_slide(slide.index).logo_uids
        return [s for s in slide.all_shapes() if s.ref.uid in ids]

    def page_number_for(self, slide_index: int) -> PageNumberObservation | None:
        for observation in self.page_numbers:
            if observation.slide_index == slide_index:
                return observation
        return None

    @property
    def primary_logo_sha1(self) -> str | None:
        return self.logo_sha1s[0] if self.logo_sha1s else None


def detect_furniture(deck: DeckModel, profile: Profile | None = None) -> Furniture:
    """Identify logo, page-number and boilerplate shapes across the deck."""
    logo_sha1s, logo_support = _logo_hashes(deck, profile)
    page_numbers = _page_numbers(deck, profile)
    boilerplate = _boilerplate(deck, profile)

    boilerplate_texts = set(boilerplate)
    page_number_ids = {(o.slide_index, o.uid) for o in page_numbers}

    by_slide: dict[int, SlideFurniture] = {}
    for slide in deck.slides:
        logos: set[int] = set()
        numbers: set[int] = set()
        plate: set[int] = set()
        for shape in slide.all_shapes():
            if shape.image_sha1 and shape.image_sha1 in logo_sha1s:
                logos.add(shape.ref.uid)
            if (slide.index, shape.ref.uid) in page_number_ids:
                numbers.add(shape.ref.uid)
            if shape.has_text and normalise_text(shape.text) in boilerplate_texts:
                plate.add(shape.ref.uid)
        text_chrome = frozenset(logos) | frozenset(numbers) | frozenset(plate)
        by_slide[slide.index] = SlideFurniture(
            logo_uids=frozenset(logos),
            page_number_uids=frozenset(numbers),
            boilerplate_uids=frozenset(plate),
            lockup_uids=_lockup_plates(slide, text_chrome),
        )

    return Furniture(
        by_slide=by_slide,
        logo_sha1s=logo_sha1s,
        logo_support=logo_support,
        page_numbers=page_numbers,
        boilerplate=boilerplate,
    )


@dataclass(frozen=True, slots=True)
class LogoMark:
    """One logo on one slide: an image part, or a lockup of drawn shapes.

    The rules and the deriver both measure a logo's placement, and both used to
    do it against a ``ShapeModel`` -- which works only where the mark is a single
    image. A mark drawn in vector is several shapes, so what they need is the
    box those shapes occupy together.
    """

    ref: ShapeRef
    left_pt: float
    top_pt: float
    width_pt: float
    height_pt: float
    #: Native pixel size where the mark is an image. ``None`` for a drawn lockup,
    #: which has no native size for a rendered one to be distorted against.
    image_pixel_width: int | None = None
    image_pixel_height: int | None = None

    @property
    def bbox_pt(self) -> tuple[float, float, float, float]:
        return (self.left_pt, self.top_pt, self.width_pt, self.height_pt)

    @property
    def aspect_ratio(self) -> float | None:
        return self.width_pt / self.height_pt if self.height_pt else None


def logo_marks(
    slide: SlideModel,
    *,
    image_sha1: frozenset[str] = frozenset(),
    lockup_text: frozenset[str] = frozenset(),
) -> list[LogoMark]:
    """Every logo on ``slide``, by whichever identity the profile learned.

    An image mark is one shape and therefore one mark. A lockup is the pieces
    carrying the learned strings, plus the plates behind them, grouped by
    proximity -- because a slide can carry the mark twice, as a divider often
    does, and their union would be a box spanning the slide.
    """
    marks = [
        LogoMark(
            ref=shape.ref,
            left_pt=shape.left_pt,
            top_pt=shape.top_pt,
            width_pt=shape.width_pt,
            height_pt=shape.height_pt,
            image_pixel_width=shape.image_pixel_width,
            image_pixel_height=shape.image_pixel_height,
        )
        for shape in slide.all_shapes()
        if shape.image_sha1 and shape.image_sha1 in image_sha1
    ]
    if not lockup_text:
        return marks

    pieces = [
        shape
        for shape in slide.leaf_shapes()
        if shape.width_pt > 0
        and shape.height_pt > 0
        and shape.has_text
        and normalise_text(shape.text) in lockup_text
    ]
    if not pieces:
        return marks
    plates = _lockup_plates(slide, frozenset(p.ref.uid for p in pieces))
    pieces += [s for s in slide.leaf_shapes() if s.ref.uid in plates]

    for group in _group_by_proximity(pieces):
        left = min(s.left_pt for s in group)
        top = min(s.top_pt for s in group)
        marks.append(
            LogoMark(
                ref=min(group, key=lambda s: s.ref.uid).ref,
                left_pt=left,
                top_pt=top,
                width_pt=max(s.right_pt for s in group) - left,
                height_pt=max(s.bottom_pt for s in group) - top,
            )
        )
    return marks


def _group_by_proximity(shapes: list[ShapeModel]) -> list[list[ShapeModel]]:
    """Single-linkage grouping of lockup pieces that sit together."""
    remaining = sorted(shapes, key=lambda s: (s.left_pt, s.top_pt))
    groups: list[list[ShapeModel]] = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for shape in list(remaining):
                if any(_sits_with(shape, member) for member in group):
                    group.append(shape)
                    remaining.remove(shape)
                    changed = True
        groups.append(group)
    return groups


def _sits_with(one: ShapeModel, other: ShapeModel) -> bool:
    reach = LOCKUP_GAP_HEIGHTS * max(one.height_pt, other.height_pt)
    horizontal = max(one.left_pt - other.right_pt, other.left_pt - one.right_pt)
    vertical = max(one.top_pt - other.bottom_pt, other.top_pt - one.bottom_pt)
    return horizontal <= reach and vertical <= reach


def lockup_strings(deck: DeckModel, boilerplate: Iterable[str]) -> frozenset[str]:
    """Which repeated strings belong to a drawn mark rather than to the furniture.

    A mark drawn in vector has no image part to be known by, so it has to be
    known by the text it sets -- and "text this deck repeats" is far too broad.
    The footer repeats. A standing callout on every slide repeats. Taking all of
    them put a deck's own body text in the logo's place.

    What distinguishes the mark is the badge. A drawn plate is set behind a
    monogram and behind almost nothing else, so the plate seeds the group and the
    wordmark beside it joins by proximity.

    A mark drawn as text alone, with no badge, is therefore not found here, and
    its geometry stays unchecked. That is deliberate: nothing separates such a
    mark from any other line the deck repeats, and guessing costs more than the
    miss does.
    """
    repeated = frozenset(boilerplate)
    strings: set[str] = set()
    for slide in deck.slides:
        chrome = frozenset(
            shape.ref.uid
            for shape in slide.leaf_shapes()
            if shape.has_text and normalise_text(shape.text) in repeated
        )
        plates = _lockup_plates(slide, chrome)
        if not plates:
            continue
        pieces = [
            shape
            for shape in slide.leaf_shapes()
            if shape.ref.uid in chrome or shape.ref.uid in plates
        ]
        for group in _group_by_proximity(pieces):
            if not any(shape.ref.uid in plates for shape in group):
                continue
            strings.update(
                normalise_text(shape.text) for shape in group if shape.has_text
            )
    return frozenset(strings)


def _lockup_plates(slide: SlideModel, text_chrome: frozenset[int]) -> frozenset[int]:
    """Drawn shapes sitting behind chrome text: the rest of a lockup.

    A house mark shipped as vector artwork is a badge with the monogram set on
    it, not an image part. Repetition across slides finds the monogram and the
    wordmark, because those are text; the badge behind them carries no text and
    no image, so nothing identified it and every layout rule measured the house
    mark as if it were content. :func:`logo_variants` already settled that logo
    identity is geometric rather than by pixels -- this is the same argument one
    step further, for a mark that has no pixels to begin with.

    Identity is containment bounded by area. A plate drawn for a glyph encloses
    that glyph and is of its order of size; a panel that happens to lie under a
    footer encloses it too but dwarfs it, so it stays content.
    """
    anchors = [
        shape
        for shape in slide.leaf_shapes()
        if shape.ref.uid in text_chrome and shape.width_pt > 0 and shape.height_pt > 0
    ]
    if not anchors:
        return frozenset()

    plates: set[int] = set()
    for shape in slide.leaf_shapes():
        if shape.has_text or shape.image_sha1 or shape.ref.uid in text_chrome:
            continue
        if shape.width_pt <= 0 or shape.height_pt <= 0:
            continue
        area = shape.width_pt * shape.height_pt
        for held in anchors:
            if area > LOCKUP_PLATE_AREA_RATIO * held.width_pt * held.height_pt:
                continue
            if _encloses(shape, held, LOCKUP_PLATE_TOLERANCE_PT):
                plates.add(shape.ref.uid)
                break
    return frozenset(plates)


def _encloses(outer: ShapeModel, inner: ShapeModel, tolerance: float) -> bool:
    return (
        outer.left_pt <= inner.left_pt + tolerance
        and outer.top_pt <= inner.top_pt + tolerance
        and outer.right_pt >= inner.right_pt - tolerance
        and outer.bottom_pt >= inner.bottom_pt - tolerance
    )


def _logo_hashes(
    deck: DeckModel, profile: Profile | None
) -> tuple[tuple[str, ...], dict[str, int]]:
    support = {sha: len(slides) for sha, slides in deck.image_sha1_slide_support.items()}
    if profile is not None and profile.brand.logo is not None:
        declared = tuple(profile.brand.logo.image_sha1)
        return declared, {sha: support.get(sha, 0) for sha in declared}

    threshold = max(1, round(LOGO_SUPPORT_SHARE * deck.slide_count))
    candidates = [sha for sha, count in support.items() if count >= threshold]
    candidates.sort(key=lambda sha: (-support[sha], sha))
    primary = tuple(candidates)
    return primary + logo_variants(deck, frozenset(primary)), support


def logo_variants(deck: DeckModel, accepted: frozenset[str]) -> tuple[str, ...]:
    """Hashes sitting in the established logo slot under a different hash.

    A brand mark usually ships in two colourways: a dark one for light slides
    and a light one for the dark ones. They are two image parts, so they have
    two hashes, and the title slide is both the slide most likely to be dark and
    often the only slide in the deck that is. Its logo therefore sits on one
    slide out of twenty and falls below :data:`LOGO_SUPPORT_SHARE`.

    The consequence was silent and worse than a missed rule. With no logo found
    on any title slide, the archetype was learned as *exempt* -- a positive
    claim that title slides correctly carry no logo -- so nothing checked the
    logo on a title slide again, and moving or deleting it was never reported.

    So identity here is geometric rather than by pixels: an image in the same
    place, at the same size, as the logo on every other slide is that logo.
    Position and size must both agree, which is what stops an ordinary picture
    that happens to share a corner from being admitted.
    """
    slots = {
        (shape.left_pt, shape.top_pt, shape.width_pt, shape.height_pt)
        for slide in deck.slides
        for shape in slide.all_shapes()
        if shape.image_sha1 and shape.image_sha1 in accepted
    }
    if not slots:
        return ()

    variants: set[str] = set()
    for slide in deck.slides:
        for shape in slide.all_shapes():
            if not shape.image_sha1 or shape.image_sha1 in accepted:
                continue
            if any(_fills_slot(shape, slot) for slot in slots):
                variants.add(shape.image_sha1)
    return tuple(sorted(variants))


def _fills_slot(shape: ShapeModel, slot: tuple[float, float, float, float]) -> bool:
    left, top, width, height = slot
    return (
        abs(shape.left_pt - left) <= LOGO_VARIANT_TOLERANCE_PT
        and abs(shape.top_pt - top) <= LOGO_VARIANT_TOLERANCE_PT
        and abs(shape.width_pt - width) <= LOGO_VARIANT_TOLERANCE_PT
        and abs(shape.height_pt - height) <= LOGO_VARIANT_TOLERANCE_PT
    )


def _page_numbers(
    deck: DeckModel, profile: Profile | None
) -> tuple[PageNumberObservation, ...]:
    """Find the shape on each slide that carries the page number.

    A shape qualifies only if it sits in the footer band, its text parses as a
    small integer, and -- across the deck -- those integers ascend. The ascending
    test is what distinguishes a page number from a column of figures that
    happens to sit low on the slide.
    """
    pattern: re.Pattern[str] | None = None
    if profile is not None and profile.brand.footer.page_number is not None:
        try:
            pattern = re.compile(profile.brand.footer.page_number.regex)
        except re.error:
            pattern = None

    candidates: list[PageNumberObservation] = []
    for slide in deck.slides:
        band_top = slide.height_pt * (1.0 - FOOTER_BAND_SHARE)
        for shape in slide.leaf_shapes():
            if not shape.has_text or shape.table is not None:
                continue
            if shape.centre_y_pt < band_top:
                continue
            text = shape.text.strip()
            value = _page_number_value(text, pattern)
            if value is None:
                continue
            candidates.append(
                PageNumberObservation(
                    slide_index=slide.index,
                    uid=shape.ref.uid,
                    value=value,
                    text=text,
                    box_pt=shape.bbox_pt,
                )
            )

    # Keep at most one per slide: the leftmost, which is where page numbers sit in
    # every house style that puts them in a corner rather than centred.
    best: dict[int, PageNumberObservation] = {}
    for observation in candidates:
        current = best.get(observation.slide_index)
        if current is None or observation.box_pt[0] < current.box_pt[0]:
            best[observation.slide_index] = observation
    return tuple(best[index] for index in sorted(best))


def _page_number_value(text: str, pattern: re.Pattern[str] | None) -> int | None:
    if pattern is not None and not pattern.match(text):
        return None
    for candidate in _PAGE_NUMBER_CANDIDATES:
        match = candidate.match(text)
        if match:
            return int(match.group(1))
    return None


def _boilerplate(
    deck: DeckModel, profile: Profile | None
) -> dict[str, tuple[int, ...]]:
    """Normalised text -> slides carrying it, for strings repeated deck-wide."""
    if profile is not None:
        declared = {
            normalise_text(entry.text): ()
            for entry in profile.brand.footer.boilerplate
        }
        if declared:
            occurrences: dict[str, list[int]] = {key: [] for key in declared}
            for slide in deck.slides:
                for shape in slide.leaf_shapes():
                    if not shape.has_text:
                        continue
                    key = normalise_text(shape.text)
                    if key in occurrences:
                        occurrences[key].append(slide.index)
            return {key: tuple(value) for key, value in occurrences.items()}
        return {}

    counts: dict[str, set[int]] = {}
    for slide in deck.slides:
        for shape in slide.leaf_shapes():
            if not shape.has_text or shape.table is not None:
                continue
            text = shape.text.strip()
            if not text or len(text) > 200:
                continue
            counts.setdefault(normalise_text(text), set()).add(slide.index)

    threshold = max(2, round(BOILERPLATE_SUPPORT_SHARE * deck.slide_count))
    return {
        key: tuple(sorted(slides))
        for key, slides in counts.items()
        if len(slides) >= threshold and not _looks_like_a_page_number(key)
    }


def _looks_like_a_page_number(normalised: str) -> bool:
    return any(candidate.match(normalised) for candidate in _PAGE_NUMBER_CANDIDATES)


def is_confidentiality_marking(text: str) -> bool:
    """Whether a repeated footer string is a confidentiality marking."""
    return bool(_CONFIDENTIALITY_RE.search(text))


def normalise_text(text: str) -> str:
    """Normalise for repetition matching: case, whitespace and quote style.

    A footer retyped by hand differs from the original in ways no reader would
    notice, and treating those as different strings would have the boilerplate
    deriver find nothing at all.
    """
    lowered = text.strip().casefold()
    lowered = lowered.replace("’", "'").replace("‘", "'")
    lowered = lowered.replace("“", '"').replace("”", '"')
    lowered = lowered.replace("–", "-").replace("—", "-")
    lowered = lowered.replace(" ", " ")
    return re.sub(r"\s+", " ", lowered)


# --------------------------------------------------------------------------------------
# Text roles
# --------------------------------------------------------------------------------------


def font_role(
    slide: SlideModel,
    shape: ShapeModel,
    *,
    furniture: Furniture | None = None,
    in_table: bool = False,
    in_chart: bool = False,
) -> str:
    """Infer what a piece of text is for.

    Deliberately structural rather than size-driven, with one exception noted
    below, because inferring a role from size and then deriving a size band per
    role would be circular.

    The exception: text in the footer band, and all body text on a disclaimer
    slide, is treated as ``footnote``. Both are legal or source type in every
    house style, and the alternative -- folding 7pt legal copy into the body band
    -- widens that band until LO-007 permits anything.

    A shape whose name names its role is believed ahead of its position, because
    position alone makes the role change when the shape does. See
    :data:`_NAME_ROLE_HINTS`.
    """
    if in_chart or shape.chart is not None:
        return "chart_label"
    if in_table or shape.table is not None:
        return "table"

    title = slide.title_shape
    if title is not None and shape.ref.uid == title.ref.uid:
        return "title"

    if furniture is not None and furniture.is_furniture(slide.index, shape.ref.uid):
        return "footnote"

    named = _role_from_name(shape.ref.name)
    if named is not None:
        return named

    if slide.archetype == "disclaimer":
        return "footnote"

    band_top = slide.height_pt * (1.0 - FOOTER_BAND_SHARE)
    if shape.centre_y_pt >= band_top:
        return "footnote"

    if slide.archetype in ("title", "section_divider", "appendix_divider"):
        return "subtitle"

    return "body"


def _role_from_name(name: str) -> str | None:
    """A role stated by the shape's own name, if any."""
    lowered = " ".join(name.split()).casefold()
    for fragment, role in _NAME_ROLE_HINTS:
        if fragment in lowered:
            return role
    return None


def content_shapes(
    slide: SlideModel, furniture: Furniture
) -> list[ShapeModel]:
    """Leaf shapes that carry content rather than chrome.

    Excludes furniture and zero-area shapes. This is the set every layout
    deriver and layout rule should be working from.
    """
    return [
        shape
        for shape in slide.leaf_shapes()
        if not furniture.is_furniture(slide.index, shape.ref.uid)
        and shape.width_pt > 0
        and shape.height_pt > 0
    ]
