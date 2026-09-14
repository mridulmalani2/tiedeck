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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from tieout.model.deck import DeckModel, ShapeModel, SlideModel

if TYPE_CHECKING:
    from tieout.profile.schema import Profile

#: An image on at least this share of slides is a logo candidate (section 8.3).
LOGO_SUPPORT_SHARE: Final[float] = 0.40

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

    logo_shape_ids: frozenset[int] = frozenset()
    page_number_shape_ids: frozenset[int] = frozenset()
    boilerplate_shape_ids: frozenset[int] = frozenset()

    @property
    def all_ids(self) -> frozenset[int]:
        return (
            self.logo_shape_ids | self.page_number_shape_ids | self.boilerplate_shape_ids
        )

    def contains(self, shape_id: int) -> bool:
        return shape_id in self.all_ids


@dataclass(frozen=True, slots=True)
class PageNumberObservation:
    """One page number found on a slide, with the value it displayed."""

    slide_index: int
    shape_id: int
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

    def is_furniture(self, slide_index: int, shape_id: int) -> bool:
        return self.for_slide(slide_index).contains(shape_id)

    def is_logo(self, slide_index: int, shape_id: int) -> bool:
        return shape_id in self.for_slide(slide_index).logo_shape_ids

    def logo_shapes(self, slide: SlideModel) -> list[ShapeModel]:
        ids = self.for_slide(slide.index).logo_shape_ids
        return [s for s in slide.all_shapes() if s.ref.shape_id in ids]

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
    page_number_ids = {(o.slide_index, o.shape_id) for o in page_numbers}

    by_slide: dict[int, SlideFurniture] = {}
    for slide in deck.slides:
        logos: set[int] = set()
        numbers: set[int] = set()
        plate: set[int] = set()
        for shape in slide.all_shapes():
            if shape.image_sha1 and shape.image_sha1 in logo_sha1s:
                logos.add(shape.ref.shape_id)
            if (slide.index, shape.ref.shape_id) in page_number_ids:
                numbers.add(shape.ref.shape_id)
            if shape.has_text and normalise_text(shape.text) in boilerplate_texts:
                plate.add(shape.ref.shape_id)
        by_slide[slide.index] = SlideFurniture(
            logo_shape_ids=frozenset(logos),
            page_number_shape_ids=frozenset(numbers),
            boilerplate_shape_ids=frozenset(plate),
        )

    return Furniture(
        by_slide=by_slide,
        logo_sha1s=logo_sha1s,
        logo_support=logo_support,
        page_numbers=page_numbers,
        boilerplate=boilerplate,
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
    return tuple(candidates), support


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
                    shape_id=shape.ref.shape_id,
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
    """
    if in_chart or shape.chart is not None:
        return "chart_label"
    if in_table or shape.table is not None:
        return "table"

    title = slide.title_shape
    if title is not None and shape.ref.shape_id == title.ref.shape_id:
        return "title"

    if furniture is not None and furniture.is_furniture(slide.index, shape.ref.shape_id):
        return "footnote"

    if slide.archetype == "disclaimer":
        return "footnote"

    band_top = slide.height_pt * (1.0 - FOOTER_BAND_SHARE)
    if shape.centre_y_pt >= band_top:
        return "footnote"

    if slide.archetype in ("title", "section_divider", "appendix_divider"):
        return "subtitle"

    return "body"


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
        if not furniture.is_furniture(slide.index, shape.ref.shape_id)
        and shape.width_pt > 0
        and shape.height_pt > 0
    ]
