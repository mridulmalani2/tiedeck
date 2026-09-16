"""Deriving the brand profile: palette, fonts, logo and footer.

The hardest of the derivers, because the brand is the part of a house style that
is most consistently applied and therefore the part where a false positive is
most damaging. A palette rule that fires on a correct slide is worse than no
palette rule.

Three deliberate conservatisms:

* A colour seen on one slide only, and covering almost none of the deck, is
  discarded rather than admitted to the palette, and is reported to the user as
  a possible error in their own reference deck. A single ``#1F3865`` on slide 12
  is almost certainly a typo for ``#1F3864``; adding it to the palette would
  licence the typo forever. A colour that recurs across slides is kept however
  little area it covers, because an accent is not a typo.
* The palette tolerance is derived from the *minimum inter-cluster distance*,
  halved, so it can never be wide enough to merge two real brand colours.
* A font role with few distinct observed sizes gets an exact allowed set rather
  than a band. A band invented from two observations permits sizes the client has
  never used.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Final

from tieout.cluster import derive_tolerance
from tieout.learn.classify import (
    Classification,
    Derivation,
    ObservationClass,
    QuestionDraft,
    classify_numeric,
    min_support_for,
)
from tieout.learn.observe import (
    DECK,
    Observation,
    archetype_scope,
    archetypes_present,
    iter_chart_fonts,
    iter_runs,
    learnable_slides,
)
from tieout.model.color import Rgb, delta_e_76, parse_hex, rgb_to_lab, try_parse_hex
from tieout.model.deck import DeckModel, ShapeModel
from tieout.model.furniture import (
    Furniture,
    is_confidentiality_marking,
    logo_variants,
    normalise_text,
)
from tieout.profile.schema import (
    BoilerplateEntry,
    Box,
    BrandProfile,
    FontRole,
    FontsProfile,
    FooterProfile,
    LogoProfile,
    PageNumberProfile,
)

#: A colour cluster below this share of total coloured weight is discarded --
#: unless it recurs, see :data:`PALETTE_MIN_SLIDES`.
PALETTE_MIN_SHARE: Final[float] = 0.005

#: Slides a low-area colour must appear on to be read as a deliberate accent
#: rather than a mistake.
#:
#: Weight alone was the whole test, and it is the wrong test for an accent. A
#: gold used for rule lines, chart markers and a few key figures covers well
#: under half a percent of a deck's coloured area while appearing on four slides
#: in five: unmistakably part of the design, and discarded as "a likely
#: reference-deck error". Every shape using it was then reported against a
#: palette that had just been told to leave it out.
#:
#: Two slides, because repetition across slides is what this tool treats as
#: convention everywhere else -- boilerplate, the logo, the grid -- and a typo
#: appears once. The docstring above always described this rule ("a colour seen
#: once is discarded"); only the code disagreed.
PALETTE_MIN_SLIDES: Final[int] = 2

#: Clustering tolerance in Lab space, per section 8.2.
PALETTE_CLUSTER_DELTA_E: Final[float] = 3.0

#: Floor for the derived palette tolerance, per section 8.3.
PALETTE_TOLERANCE_FLOOR: Final[float] = 2.0

#: Ceiling for the derived palette tolerance. Not in the specification.
#:
#: Section 8.3 derives the tolerance as half the minimum inter-cluster distance,
#: floored at 2.0, which guarantees it can never merge two brand colours. But a
#: five-colour palette of black, white, navy, grey and red has a minimum
#: separation above 30 Delta-E, giving a tolerance of 16 -- wide enough that a
#: visibly wrong colour passes. The floor protects against a tolerance that is
#: too tight; this protects against one so loose the rule stops working. Recorded
#: in the profile's provenance so the user can see it was applied.
PALETTE_TOLERANCE_CEILING: Final[float] = 6.0

#: A typeface below this share of characters is not part of the approved set.
FONT_MIN_SHARE: Final[float] = 0.01

#: Below this many distinct sizes a role gets an exact set, not a band.
ROLE_BAND_MIN_DISTINCT: Final[int] = 4

#: An image on at least this share of slides is a logo candidate.
LOGO_SUPPORT_SHARE: Final[float] = 0.40

#: Two logo candidates whose supports are within this ratio are ambiguous.
LOGO_AMBIGUITY_RATIO: Final[float] = 0.75

#: A string repeated on at least this share of slides is required boilerplate.
BOILERPLATE_SUPPORT_SHARE: Final[float] = 0.60


@dataclass
class PaletteCluster:
    """One colour the deck actually uses, with the weight behind it."""

    rgb: Rgb
    weight: float
    members: list[tuple[Rgb, int]] = field(default_factory=list)

    @property
    def hex(self) -> str:
        return self.rgb.hex

    @property
    def support(self) -> int:
        return len(self.members)

    @property
    def slides(self) -> tuple[int, ...]:
        return tuple(sorted({slide for _, slide in self.members}))

    @property
    def radius(self) -> float:
        """The furthest any member sits from this cluster's representative.

        BR-004 measures every colour in the deck against the representative, so
        this is the distance the tolerance has to admit for the reference deck
        to pass its own palette.
        """
        return max(
            (delta_e_76(self.rgb, member) for member, _ in self.members),
            default=0.0,
        )


@dataclass
class BrandDerivation:
    """The derived brand profile plus everything the emitter needs to explain it."""

    profile: BrandProfile
    derivation: Derivation = field(default_factory=Derivation)


def derive_brand(deck: DeckModel, furniture: Furniture) -> BrandDerivation:
    """Derive the whole brand profile from one or more reference decks."""
    result = BrandDerivation(profile=BrandProfile())
    total_slides = deck.slide_count

    _derive_palette(deck, result, total_slides)
    _derive_fonts(deck, furniture, result, total_slides)
    _derive_logo(deck, furniture, result, total_slides)
    _derive_footer(deck, furniture, result, total_slides)
    return result


# --------------------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------------------


def collect_colour_weights(deck: DeckModel) -> list[tuple[Rgb, float, int]]:
    """Every resolved colour in the deck with the weight it carries.

    Fills and lines are weighted by the area they cover; text by the characters
    it sets. Weighting by shape count instead would let a 1.5pt hairline rule
    outvote a full-bleed background, and the derived palette would be ordered by
    how many decorative strokes a designer happened to draw.

    Chart series colours are not collected. TieOut does not model chart geometry,
    so there is no area to weight them by, and a chart's palette is set by the
    theme rather than typed by the author. Stated as a limitation in the README.

    Furniture is deliberately included: the footer and the logo are part of the
    brand, and a palette that excluded them would fail BR-004 on the very
    elements the house style is most precise about.
    """
    out: list[tuple[Rgb, float, int]] = []
    for slide in learnable_slides(deck):
        slide_area = slide.area_pt2 or 1.0
        for shape in slide.leaf_shapes():
            area_weight = (shape.area_pt2 / slide_area) * 100.0

            fill = shape.effective_fill
            if fill is not None and fill.is_solid:
                colour = try_parse_hex(fill.hex)
                if colour is not None:
                    out.append((colour, area_weight, slide.index))

            line = shape.effective_line
            if line is not None and line.is_visible and line.hex:
                colour = try_parse_hex(line.hex)
                if colour is not None:
                    # A stroke covers its perimeter times its width, not the
                    # shape's area.
                    perimeter = 2 * (shape.width_pt + shape.height_pt)
                    stroke_area = perimeter * max(line.width_pt or 0.75, 0.75)
                    out.append((colour, (stroke_area / slide_area) * 100.0, slide.index))

            if shape.table is not None:
                for cell in shape.table.cells:
                    if cell.is_merge_continuation or cell.fill is None:
                        continue
                    if not cell.fill.is_solid:
                        continue
                    colour = try_parse_hex(cell.fill.hex)
                    if colour is not None:
                        out.append((colour, area_weight / max(1, shape.table.row_count),
                                    slide.index))

            for paragraph in shape.all_paragraphs:
                for run in paragraph.runs:
                    text = run.text.strip()
                    if not text:
                        continue
                    colour = run.font.rgb
                    if colour is not None:
                        out.append((colour, float(len(text)), slide.index))

    return out


def cluster_palette(
    weighted: list[tuple[Rgb, float, int]],
    tolerance_delta_e: float = PALETTE_CLUSTER_DELTA_E,
) -> list[PaletteCluster]:
    """Agglomerate colours in Lab space, heaviest first.

    Greedy rather than optimal, and deliberately so: processing in descending
    weight order makes the outcome deterministic and makes the heaviest colour
    the cluster's representative, which is the value a user recognises as their
    brand colour. An optimal clustering could put the representative at a
    weighted centroid no element of the deck actually uses.
    """
    totals: dict[str, tuple[Rgb, float, list[tuple[Rgb, int]]]] = {}
    for colour, weight, slide_index in weighted:
        key = colour.hex
        existing = totals.get(key)
        if existing is None:
            totals[key] = (colour, weight, [(colour, slide_index)])
        else:
            totals[key] = (
                existing[0],
                existing[1] + weight,
                [*existing[2], (colour, slide_index)],
            )

    ordered = sorted(totals.values(), key=lambda item: (-item[1], item[0].hex))

    clusters: list[PaletteCluster] = []
    for colour, weight, members in ordered:
        lab = rgb_to_lab(colour)
        for cluster in clusters:
            if delta_e_76(lab, cluster.rgb) <= tolerance_delta_e:
                cluster.weight += weight
                cluster.members.extend(members)
                break
        else:
            clusters.append(
                PaletteCluster(rgb=colour, weight=weight, members=list(members))
            )
    clusters.sort(key=lambda c: (-c.weight, c.hex))
    return clusters


def derive_palette_tolerance(clusters: list[PaletteCluster]) -> tuple[float, str]:
    """Half the minimum inter-cluster distance, floored to admit the deck.

    Two constraints pull in opposite directions and both have to hold.

    *Discrimination*: the tolerance must stay below half the distance between
    two palette entries, or a colour would read as "on the palette" for two
    entries at once and the rule would stop telling them apart.

    *Admission*: the tolerance must be at least each cluster's radius -- the
    furthest a member sits from the representative BR-004 measures against.
    Clustering gathers colours within :data:`PALETTE_CLUSTER_DELTA_E` of each
    other, so a cluster can be 3 Delta-E wide while the derived tolerance is 2;
    the rim of every cluster then fails a check against the palette it is part
    of. That is not a deck with off-palette colours, it is a profile that
    disagrees with itself, and on a real deck it was ten of the seventeen
    colour findings a clean deck produced against its own profile.

    Admission wins where they conflict, and the reason says so: a tolerance that
    fails the deck it was learned from is wrong in a way no user can act on,
    while one that is slightly generous merely reports less.

    Returns the tolerance and the sentence explaining it.
    """
    radius = max((cluster.radius for cluster in clusters), default=0.0)
    # A hair above the radius: at exactly the radius the comparison is an
    # equality on floating point, and the member on the rim can fall either way.
    admission = round(radius + 0.1, 2)

    if len(clusters) < 2:
        floor = max(PALETTE_TOLERANCE_FLOOR, admission)
        return (
            floor,
            f"floored at {floor:g}: fewer than two palette clusters, so no "
            f"inter-cluster distance to derive from"
            + (
                f"; raised to admit the widest cluster, whose members reach "
                f"{radius:.1f} Delta-E from it"
                if admission > PALETTE_TOLERANCE_FLOOR
                else ""
            ),
        )

    minimum = min(
        delta_e_76(a.rgb, b.rgb)
        for index, a in enumerate(clusters)
        for b in clusters[index + 1 :]
    )
    half = minimum / 2.0
    tolerance = max(PALETTE_TOLERANCE_FLOOR, min(half, PALETTE_TOLERANCE_CEILING))
    if admission > tolerance:
        tolerance = min(admission, PALETTE_TOLERANCE_CEILING)
        return (
            round(tolerance, 2),
            f"half the minimum inter-cluster distance is {half:.1f}, raised to "
            f"{tolerance:g} so the palette admits the deck it was learned from: "
            f"the widest cluster's members reach {radius:.1f} Delta-E from the "
            f"colour that represents them",
        )
    if half > PALETTE_TOLERANCE_CEILING:
        reason = (
            f"half the minimum inter-cluster distance is {half:.1f}, capped at "
            f"{PALETTE_TOLERANCE_CEILING:g} so the rule retains discriminating power"
        )
    elif half < PALETTE_TOLERANCE_FLOOR:
        reason = (
            f"half the minimum inter-cluster distance is {half:.1f}, raised to the "
            f"floor of {PALETTE_TOLERANCE_FLOOR:g}"
        )
    else:
        reason = (
            f"half the minimum inter-cluster distance of {minimum:.1f} Delta-E "
            f"between palette colours"
        )
    return round(tolerance, 2), reason


def _derive_palette(
    deck: DeckModel, result: BrandDerivation, total_slides: int
) -> None:
    weighted = collect_colour_weights(deck)
    if not weighted:
        result.derivation.unlearned(
            "brand.palette_hex", "no resolved colours found in the reference deck"
        )
        return

    clusters = cluster_palette(weighted)
    total_weight = sum(c.weight for c in clusters) or 1.0

    def _is_house_colour(cluster: PaletteCluster) -> bool:
        if cluster.weight / total_weight >= PALETTE_MIN_SHARE:
            return True
        return len(set(cluster.slides)) >= PALETTE_MIN_SLIDES

    kept = [c for c in clusters if _is_house_colour(c)]
    discarded = [c for c in clusters if not _is_house_colour(c)]

    if not kept:
        result.derivation.unlearned(
            "brand.palette_hex",
            "every colour cluster fell below the 0.5% weight threshold",
        )
        return

    result.profile.palette_hex = [c.hex for c in kept]
    tolerance, tolerance_reason = derive_palette_tolerance(kept)
    result.profile.palette_tolerance_delta_e = tolerance
    result.profile.palette_discarded = [c.hex for c in discarded]

    shares = ", ".join(
        f"{c.hex} {c.weight / total_weight:.1%}" for c in kept
    )
    result.derivation.note(
        "brand.palette_hex",
        f"area and character weighted colour clusters across {total_slides} "
        f"slides: {shares}",
        "high",
    )
    result.derivation.note(
        "brand.palette_tolerance_delta_e", tolerance_reason, "high"
    )

    if discarded:
        nearest_notes: list[str] = []
        for cluster in discarded[:8]:
            closest = min(kept, key=lambda k: delta_e_76(k.rgb, cluster.rgb))
            distance = delta_e_76(closest.rgb, cluster.rgb)
            slides = ", ".join(str(s) for s in cluster.slides[:3])
            nearest_notes.append(
                f"{cluster.hex} on slide{'s' if len(cluster.slides) > 1 else ''} "
                f"{slides} (Delta-E {distance:.1f} from {closest.hex})"
            )
        result.derivation.note(
            "brand.palette_discarded",
            "discarded as likely reference-deck errors, not added to the palette: "
            + "; ".join(nearest_notes),
            "medium",
        )


# --------------------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------------------


def _derive_fonts(
    deck: DeckModel,
    furniture: Furniture,
    result: BrandDerivation,
    total_slides: int,
) -> None:
    name_weights: dict[str, float] = {}
    role_sizes: dict[str, list[tuple[float, float, int]]] = {}

    for context in iter_runs(deck, furniture, include_furniture=True):
        if context.slide.archetype == "unknown":
            continue
        font = context.run.font
        weight = context.weight
        if font.name:
            name_weights[font.name] = name_weights.get(font.name, 0.0) + weight
        if font.size_pt is not None:
            role_sizes.setdefault(context.role, []).append(
                (font.size_pt, weight, context.slide.index)
            )

    for slide, shape, font in iter_chart_fonts(deck):
        if slide.archetype == "unknown":
            continue
        weight = float(sum(len(s) for s in (shape.chart.text_strings if shape.chart else ())))
        if font.name:
            name_weights[font.name] = name_weights.get(font.name, 0.0) + weight
        if font.size_pt is not None:
            role_sizes.setdefault("chart_label", []).append(
                (font.size_pt, weight, slide.index)
            )

    fonts = FontsProfile()
    total = sum(name_weights.values()) or 1.0
    allowed = [
        name
        for name, weight in sorted(name_weights.items(), key=lambda i: (-i[1], i[0]))
        if weight / total >= FONT_MIN_SHARE
    ]
    if allowed:
        fonts.allowed = allowed
        shares = ", ".join(
            f"{name} {name_weights[name] / total:.1%}" for name in allowed
        )
        result.derivation.note(
            "brand.fonts.allowed",
            f"character-weighted typefaces across {total_slides} slides: {shares}",
            "high",
        )
        rejected = [n for n in name_weights if n not in allowed]
        if rejected:
            result.derivation.note(
                "brand.fonts.allowed.rejected",
                "below the 1% character threshold and not admitted: "
                + ", ".join(
                    f"{n} {name_weights[n] / total:.2%}" for n in sorted(rejected)
                ),
                "medium",
            )
    else:
        result.derivation.unlearned(
            "brand.fonts.allowed", "no resolved typefaces found in the reference deck"
        )

    for role, entries in sorted(role_sizes.items()):
        path = f"brand.fonts.roles.{role}"
        classification = _classify_sizes(role, entries)
        if not classification.learned:
            result.derivation.unlearned(path, classification.reason)
            continue

        distinct = sorted({size for size, _, _ in entries})
        if len(distinct) < ROLE_BAND_MIN_DISTINCT:
            fonts.roles[role] = FontRole(exact_pt=distinct)
            result.derivation.note(
                path,
                f"{len(distinct)} distinct size"
                f"{'s' if len(distinct) != 1 else ''} observed across "
                f"{classification.support} runs on "
                f"{len(set(classification.slides))} slides: "
                + ", ".join(f"{d:g}pt" for d in distinct)
                + "; emitted as an exact set because fewer than "
                f"{ROLE_BAND_MIN_DISTINCT} distinct sizes do not describe a band",
                classification.confidence,
            )
        else:
            fonts.roles[role] = FontRole(min_pt=min(distinct), max_pt=max(distinct))
            result.derivation.note(
                path,
                f"{len(distinct)} distinct sizes observed across "
                f"{classification.support} runs on "
                f"{len(set(classification.slides))} slides "
                f"({', '.join(f'{d:g}' for d in distinct)}pt); emitted as a band",
                classification.confidence,
            )

    result.profile.fonts = fonts


def _classify_sizes(
    role: str, entries: list[tuple[float, float, int]]
) -> Classification:
    observations = [
        Observation(f"font.{role}.size_pt", DECK, size, slide, weight)
        for size, weight, slide in entries
    ]
    # Font sizes cluster at zero tolerance: 10pt and 11pt are different sizes, not
    # two readings of one size.
    classification = classify_numeric(
        f"font.{role}.size_pt", DECK, observations, tolerance=0.0
    )
    # A role with several legitimate sizes is not "variable"; it is a band. Only
    # thin evidence should prevent a role being learned at all.
    if (
        not classification.learned
        and classification.support >= min_support_for(DECK)
    ):
        classification.observation_class = ObservationClass.MULTIMODAL
        classification.confidence = "medium"
        classification.reason = ""
    return classification


# --------------------------------------------------------------------------------------
# Logo
# --------------------------------------------------------------------------------------


def _derive_logo(
    deck: DeckModel,
    furniture: Furniture,
    result: BrandDerivation,
    total_slides: int,
) -> None:
    support = {
        sha: len(slides) for sha, slides in deck.image_sha1_slide_support.items()
    }
    if not support:
        result.derivation.unlearned(
            "brand.logo", "the reference deck contains no images"
        )
        return

    threshold = max(1, round(LOGO_SUPPORT_SHARE * total_slides))
    candidates = sorted(
        (sha for sha, count in support.items() if count >= threshold),
        key=lambda sha: (-support[sha], sha),
    )
    if not candidates:
        best = max(support.items(), key=lambda item: (item[1], item[0]))
        result.derivation.unlearned(
            "brand.logo",
            f"no image appears on at least {LOGO_SUPPORT_SHARE:.0%} of slides; the "
            f"most repeated appears on {best[1]} of {total_slides}",
        )
        return

    primary = candidates[0]
    # A mark in two colourways is two image parts and so two hashes, and the
    # light one is often carried by the title slide alone -- below the support
    # threshold, and so invisible here. Admitted by the slot it fills rather
    # than by its pixels, and never at the expense of the ambiguity question
    # below: a runner-up that cleared the threshold on its own is a candidate,
    # not a variant, and is still asked about.
    variants = tuple(
        sha
        for sha in logo_variants(deck, frozenset({primary}))
        if sha not in set(candidates)
    )
    logo = LogoProfile(image_sha1=[primary, *variants])
    result.derivation.note(
        "brand.logo",
        f"one image present on {support[primary]} of {total_slides} slides"
        + (
            f"; {len(variants)} further image(s) in the same position and at the same "
            f"size, read as the same mark in another colourway"
            if variants
            else ""
        ),
        "high",
    )

    if len(candidates) > 1:
        runner_up = candidates[1]
        ratio = support[runner_up] / support[primary]
        if ratio >= LOGO_AMBIGUITY_RATIO:
            result.derivation.ask(
                _logo_variant_question(deck, primary, runner_up, support)
            )
        else:
            result.derivation.note(
                "brand.logo.secondary",
                f"a second repeated image appears on {support[runner_up]} of "
                f"{total_slides} slides, too few to be the primary mark",
                "medium",
            )

    _derive_logo_boxes(deck, frozenset({primary, *variants}), logo, result, total_slides)
    result.profile.logo = logo


def _derive_logo_boxes(
    deck: DeckModel,
    sha1s: frozenset[str],
    logo: LogoProfile,
    result: BrandDerivation,
    total_slides: int,
) -> None:
    """Expected box per archetype, with archetypes that never carry it exempted."""
    by_archetype: dict[str, list[ShapeModel]] = {}
    for slide in learnable_slides(deck):
        for shape in slide.all_shapes():
            if shape.image_sha1 in sha1s:
                by_archetype.setdefault(slide.archetype, []).append(shape)

    aspects: list[float] = []
    for archetype in archetypes_present(deck):
        path = f"brand.logo.per_archetype.{archetype}"
        shapes = by_archetype.get(archetype, [])
        slides_in_archetype = len(
            [s for s in learnable_slides(deck) if s.archetype == archetype]
        )

        if not shapes:
            logo.per_archetype[archetype] = "exempt"
            result.derivation.note(
                path,
                f"logo absent on all {slides_in_archetype} {archetype} slide"
                f"{'s' if slides_in_archetype != 1 else ''}, so the archetype is "
                f"exempt rather than unchecked",
                "high",
            )
            continue

        # An archetype with a single slide is inherently singular, like the slide
        # dimensions: there is no second placement that could disagree. Demanding
        # two observations there would leave the one agenda slide's logo
        # unchecked forever, which is worse than a medium-confidence rule.
        required_support = 1 if slides_in_archetype == 1 else None

        edges: dict[str, Classification] = {}
        for name, values in (
            ("left", [(s.left_pt, s.ref.slide_index) for s in shapes]),
            ("top", [(s.top_pt, s.ref.slide_index) for s in shapes]),
            ("width", [(s.width_pt, s.ref.slide_index) for s in shapes]),
            ("height", [(s.height_pt, s.ref.slide_index) for s in shapes]),
        ):
            observations = [
                Observation(f"logo.{name}", archetype_scope(archetype), value, slide_index)
                for value, slide_index in values
            ]
            edges[name] = classify_numeric(
                f"logo.{name}",
                archetype_scope(archetype),
                observations,
                tolerance=2.0,
                min_support=required_support,
            )

        if not all(c.learned for c in edges.values()):
            unlearned = [n for n, c in edges.items() if not c.learned]
            result.derivation.unlearned(
                path,
                f"logo geometry inconsistent on {archetype} slides: "
                + "; ".join(f"{n}: {edges[n].reason}" for n in unlearned),
            )
            continue

        spread = max(c.spread for c in edges.values())
        tolerance = derive_tolerance(spread, floor=2.0)
        logo.per_archetype[archetype] = Box(
            left=round(float(edges["left"].value), 2),
            top=round(float(edges["top"].value), 2),
            width=round(float(edges["width"].value), 2),
            height=round(float(edges["height"].value), 2),
            tolerance_pt=tolerance,
        )
        worst = min(edges.values(), key=lambda c: c.top_share)
        confidence = max(
            (c.confidence for c in edges.values()),
            key=lambda c: {"high": 0, "medium": 1, "low": 2}[c],
        )
        if len(shapes) == 1:
            confidence = "medium"
            detail = (
                f"logo geometry from the single {archetype} slide in the reference "
                f"deck, so there is no second placement to corroborate it"
            )
        else:
            detail = (
                f"logo geometry on {len(shapes)} {archetype} slides, "
                f"{worst.observation_class.value} with an observed spread of "
                f"{spread:.1f}pt giving a tolerance of {tolerance:g}pt"
            )
        result.derivation.note(path, detail, confidence)

        for shape in shapes:
            if shape.height_pt > 0:
                aspects.append(shape.width_pt / shape.height_pt)

        outliers = [o for c in edges.values() for o in c.outliers]
        if outliers:
            result.derivation.ask(
                _logo_outlier_question(archetype, shapes, edges, path)
            )

    if aspects:
        nominal = sorted(aspects)[len(aspects) // 2]
        deviation = max(abs(a / nominal - 1.0) for a in aspects)
        logo.aspect_ratio_tolerance = max(0.01, round(deviation * 1.5, 4))
        result.derivation.note(
            "brand.logo.aspect_ratio_tolerance",
            f"median aspect ratio {nominal:.3f} across {len(aspects)} placements, "
            f"worst observed deviation {deviation:.2%}",
            "high",
        )
    del total_slides


def _logo_outlier_question(
    archetype: str,
    shapes: list[ShapeModel],
    edges: dict[str, Classification],
    path: str,
) -> QuestionDraft:
    worst = max(edges.items(), key=lambda item: len(item[1].outliers))
    name, classification = worst
    outlier_slides = tuple(sorted({o.slide_index for o in classification.outliers}))
    del shapes
    dominant = classification.value
    return QuestionDraft(
        id=f"logo-outlier-{archetype}-{name}",
        field_path=path,
        question=(
            f"The logo's {name} is {dominant:g}pt on most {archetype} slides but "
            f"differs on {len(classification.outliers)} of them"
            + (f" (slides {', '.join(str(s) for s in outlier_slides)})"
               if outlier_slides else "")
            + ". Are those slides exceptions, or errors in the reference deck?"
        ),
        options=[
            "error, enforce the dominant value",
            "exception, leave this archetype unenforced",
        ],
        default="error, enforce the dominant value",
        impact=len(classification.outliers) * 2,
        kind="dominant_outlier",
        slides=outlier_slides,
    )


def _logo_variant_question(
    deck: DeckModel, primary: str, secondary: str, support: dict[str, int]
) -> QuestionDraft:
    """Ask which of two similarly supported marks is primary.

    Decided by the mean luminance of the slide background behind each, per
    section 8.3, because the usual reason for two marks is a reversed variant for
    dark grounds.
    """
    primary_luminance = _mean_backdrop_luminance(deck, primary)
    secondary_luminance = _mean_backdrop_luminance(deck, secondary)
    hint = ""
    if primary_luminance is not None and secondary_luminance is not None:
        darker = "second" if secondary_luminance < primary_luminance else "first"
        hint = (
            f" The {darker} sits on a darker background on average, which usually "
            f"means it is the reversed variant."
        )
    return QuestionDraft(
        id="logo-variant",
        field_path="brand.logo.image_sha1",
        question=(
            f"Two images repeat across the deck, on {support[primary]} and "
            f"{support[secondary]} slides. Which is the primary mark, and is the "
            f"other a dark-background variant?{hint}"
        ),
        options=[
            "the more frequent is primary, the other is a variant",
            "both are acceptable marks",
            "only the more frequent is acceptable",
        ],
        default="the more frequent is primary, the other is a variant",
        impact=support[primary] + support[secondary],
        kind="logo_variant",
    )


def _mean_backdrop_luminance(deck: DeckModel, sha1: str) -> float | None:
    """Mean relative luminance of the slide background wherever an image appears."""
    values: list[float] = []
    for slide in deck.slides:
        if not any(s.image_sha1 == sha1 for s in slide.all_shapes()):
            continue
        background = slide.background
        if background is None or not background.is_solid or background.hex is None:
            continue
        values.append(parse_hex(background.hex).relative_luminance)
    if not values:
        return None
    return sum(values) / len(values)


# --------------------------------------------------------------------------------------
# Footer
# --------------------------------------------------------------------------------------


def _derive_footer(
    deck: DeckModel,
    furniture: Furniture,
    result: BrandDerivation,
    total_slides: int,
) -> None:
    footer = FooterProfile()
    _derive_page_numbers(deck, furniture, footer, result, total_slides)
    _derive_boilerplate(deck, furniture, footer, result, total_slides)
    result.profile.footer = footer


def _page_number_regex(texts: list[str]) -> tuple[str, str]:
    """Infer a regex from the observed page-number strings.

    Returns the regex and a description of the form it was inferred from.
    """
    import re as _re

    if all(_re.fullmatch(r"\d{1,3}", t) for t in texts):
        return (r"^\d+$", "bare digits")
    if all(_re.fullmatch(r"[Pp]age\s+\d{1,3}", t) for t in texts):
        return (r"^[Pp]age\s+\d+$", "'Page N'")
    if all(_re.fullmatch(r"\d{1,3}\s*[|/]\s*\d{1,3}", t) for t in texts):
        return (r"^\d+\s*[|/]\s*\d+$", "'N | total'")
    if all(_re.fullmatch(r"[Pp]age\s+\d{1,3}\s+of\s+\d{1,3}", t) for t in texts):
        return (r"^[Pp]age\s+\d+\s+of\s+\d+$", "'Page N of total'")
    return (r"^\D*\d+\D*$", "mixed forms, matched loosely")


def _derive_page_numbers(
    deck: DeckModel,
    furniture: Furniture,
    footer: FooterProfile,
    result: BrandDerivation,
    total_slides: int,
) -> None:
    observations = [
        o for o in furniture.page_numbers
        if (slide := deck.slide(o.slide_index)) is not None
        and slide.archetype != "unknown"
    ]
    if len(observations) < 2:
        result.derivation.unlearned(
            "brand.footer.page_number",
            f"only {len(observations)} slide"
            f"{'s' if len(observations) != 1 else ''} carry a value that parses as "
            f"a page number, too few to derive a convention",
        )
        return

    values = [o.value for o in observations]
    ascending = all(b > a for a, b in itertools.pairwise(values))
    if not ascending:
        result.derivation.unlearned(
            "brand.footer.page_number",
            "the values that look like page numbers do not ascend across the deck, "
            "so they are probably not page numbers",
        )
        return

    regex, form = _page_number_regex([o.text for o in observations])
    carrying = {o.slide_index for o in observations}
    required_on = sorted(
        {
            slide.archetype
            for slide in learnable_slides(deck)
            if slide.index in carrying
        }
    )
    # An archetype is only "required" if every slide of that archetype carries one.
    required_on = [
        archetype
        for archetype in required_on
        if all(
            slide.index in carrying
            for slide in learnable_slides(deck)
            if slide.archetype == archetype
        )
    ]

    edges: dict[str, Classification] = {}
    for index, name in enumerate(("left", "top", "width", "height")):
        edges[name] = classify_numeric(
            f"page_number.{name}",
            DECK,
            [
                Observation(f"page_number.{name}", DECK, o.box_pt[index], o.slide_index)
                for o in observations
            ],
            tolerance=2.0,
        )

    box: Box | None = None
    if all(c.learned for c in edges.values()):
        spread = max(c.spread for c in edges.values())
        box = Box(
            left=round(float(edges["left"].value), 2),
            top=round(float(edges["top"].value), 2),
            width=round(float(edges["width"].value), 2),
            height=round(float(edges["height"].value), 2),
            tolerance_pt=derive_tolerance(spread, floor=2.0),
        )
    else:
        result.derivation.unlearned(
            "brand.footer.page_number.box_pt",
            "page number position is not consistent across the slides that carry it",
        )

    footer.page_number = PageNumberProfile(
        required_on=required_on, regex=regex, box_pt=box, must_ascend=True
    )
    result.derivation.note(
        "brand.footer.page_number",
        f"{len(observations)} ascending page numbers in {form} on "
        f"{len(carrying)} of {total_slides} slides, required on "
        + (", ".join(required_on) if required_on else "no archetype consistently"),
        "high",
    )


def _derive_boilerplate(
    deck: DeckModel,
    furniture: Furniture,
    footer: FooterProfile,
    result: BrandDerivation,
    total_slides: int,
) -> None:
    threshold = max(2, round(BOILERPLATE_SUPPORT_SHARE * total_slides))
    learnable = learnable_slides(deck)
    learnable_indices = {s.index for s in learnable}

    for normalised, slides in sorted(furniture.boilerplate.items()):
        present = {index for index in slides if index in learnable_indices}
        if len(present) < threshold:
            continue

        surface = _surface_form(deck, normalised)
        archetypes = sorted(
            {
                slide.archetype
                for slide in learnable
                if slide.index in present
            }
        )
        required_on = [
            archetype
            for archetype in archetypes
            if all(
                slide.index in present
                for slide in learnable
                if slide.archetype == archetype
            )
        ]
        is_confidentiality = is_confidentiality_marking(normalised)
        footer.boilerplate.append(
            BoilerplateEntry(
                text=surface,
                required_on=required_on,
                is_confidentiality=is_confidentiality,
            )
        )
        result.derivation.note(
            f"brand.footer.boilerplate.{surface}",
            f"repeated verbatim on {len(present)} of {total_slides} slides, "
            f"required on " + (", ".join(required_on) if required_on else "no archetype"),
            "high",
        )

        # Only worth asking when the string is a confidentiality marking that is
        # NOT on every archetype. If it is everywhere, there is nothing ambiguous
        # to ask about, and asking anyway is how a twelve-question budget gets
        # wasted.
        missing = sorted(set(archetypes) - set(required_on))
        if is_confidentiality and missing:
            result.derivation.ask(
                QuestionDraft(
                    id=f"confidentiality-scope-{surface[:24]}",
                    field_path="brand.footer.boilerplate",
                    question=(
                        f"Is {surface!r} required on every slide, or only on some? "
                        f"It is missing from some {', '.join(missing)} slides."
                    ),
                    options=[
                        "required everywhere it currently appears",
                        "required on every slide",
                        "required only on the title and disclaimer",
                    ],
                    default="required everywhere it currently appears",
                    impact=total_slides - len(present),
                    kind="confidentiality_scope",
                )
            )

    if not footer.boilerplate:
        result.derivation.unlearned(
            "brand.footer.boilerplate",
            f"no text string is repeated on at least "
            f"{BOILERPLATE_SUPPORT_SHARE:.0%} of slides",
        )


def _surface_form(deck: DeckModel, normalised: str) -> str:
    """Recover the as-typed form of a normalised boilerplate string."""
    for slide in deck.slides:
        for shape in slide.leaf_shapes():
            if shape.has_text and normalise_text(shape.text) == normalised:
                return shape.text.strip()
    return normalised
