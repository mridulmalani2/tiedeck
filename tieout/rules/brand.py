"""Brand rules: the house style a client can point at and recognise.

Every expectation in this module comes from the client's own reference deck, so
every finding carries the provenance string that says so. A brand finding without
provenance is an assertion; with it, it is a comparison the user can check.

Three conventions hold throughout:

* Colour is compared as CIE76 Delta-E in Lab space, never as a hex string. A
  designer cannot see the difference between ``#1F3864`` and ``#1F3963`` and a
  rule that reports it is a rule the user learns to ignore.
* Deviations are clustered. One off-palette fill used by six shapes on a slide is
  one finding naming six shapes, not six findings.
* An expectation the learner declined to derive is absent from the profile, and
  the engine skips the rule rather than measuring against a default the client
  never agreed to. That is what :attr:`Rule.requires` is for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import ClassVar

from tieout.model.color import Rgb, nearest, try_parse_hex
from tieout.model.deck import DeckModel, ShapeModel, ShapeRef, SlideModel
from tieout.model.furniture import normalise_text
from tieout.model.units import approx_equal
from tieout.profile.schema import Box, LogoProfile, Profile, Severity
from tieout.rules.base import Finding, Rule, register

#: Fill and line kinds that carry no single measurable colour. ``inherit`` means
#: the resolver found nothing to resolve, which is not the same as a colour.
_UNMEASURABLE_FILL_KINDS: frozenset[str] = frozenset(
    {"picture", "group", "none", "inherit"}
)


@dataclass(frozen=True, slots=True)
class _ColourUse:
    """One resolved colour, and the shape and property it came from."""

    hex: str
    ref: ShapeRef
    source: str


def _palette(profile: Profile) -> list[Rgb]:
    """The learned palette as parsed colours, skipping anything unparseable.

    A hand-edited profile can contain ``navy`` where a hex triplet belongs;
    dropping it is better than aborting the audit, and the entry's absence shows
    up as findings the user will question.
    """
    parsed = [try_parse_hex(entry) for entry in profile.brand.palette_hex]
    return [rgb for rgb in parsed if rgb is not None]


def _colour_uses(shape: ShapeModel) -> list[_ColourUse]:
    """Every resolved colour one shape puts on the slide.

    Chart internals are excluded: TieOut does not model the chart part's colour
    scheme, and series colours resolved out of context would be measured against
    a palette that was never meant to constrain them.
    """
    uses: list[_ColourUse] = []
    fill = shape.effective_fill
    if fill is not None and fill.kind not in _UNMEASURABLE_FILL_KINDS and fill.hex:
        uses.append(_ColourUse(fill.hex, shape.ref, "fill"))

    line = shape.effective_line
    if line is not None and line.is_visible and line.hex:
        uses.append(_ColourUse(line.hex, shape.ref, "outline"))

    for paragraph in shape.text_frame_paragraphs:
        for run in paragraph.runs:
            if run.font.color_hex and run.text.strip():
                uses.append(_ColourUse(run.font.color_hex, shape.ref, "text"))

    if shape.table is not None:
        for cell in shape.table.cells:
            if cell.is_merge_continuation:
                continue
            if cell.fill is not None and cell.fill.is_solid and cell.fill.hex:
                uses.append(_ColourUse(cell.fill.hex, shape.ref, "table cell fill"))
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    if run.font.color_hex and run.text.strip():
                        uses.append(_ColourUse(run.font.color_hex, shape.ref, "table text"))
    return uses


def _font_names(shape: ShapeModel) -> list[str]:
    """Every resolved typeface name in one shape, one entry per run.

    Counted per run rather than deduplicated so a finding can say how much of the
    slide is affected. ``shape.effective_font`` is deliberately not consulted: a
    shape with no text has no typeface a reader could see.
    """
    names: list[str] = []
    for paragraph in shape.all_paragraphs:
        for run in paragraph.runs:
            if run.font.name and run.text.strip():
                names.append(run.font.name)
    if shape.chart is not None:
        names.extend(font.name for font in shape.chart.fonts if font.name)
    return names


def _logo_targets(deck: DeckModel, logo: LogoProfile) -> list[tuple[SlideModel, Box]]:
    """Slides whose archetype has a learned logo box.

    Exempt archetypes are skipped because the logo is absent by design. Archetypes
    missing from the mapping are skipped because there is no evidence either way,
    and those two cases must not produce the same finding.
    """
    out: list[tuple[SlideModel, Box]] = []
    for slide in deck.slides:
        if logo.is_exempt(slide.archetype) or not logo.is_known(slide.archetype):
            continue
        box = logo.box_for(slide.archetype)
        if box is not None:
            out.append((slide, box))
    return out


def _logo_shapes(slide: SlideModel, logo: LogoProfile) -> list[ShapeModel]:
    approved = set(logo.image_sha1)
    return [s for s in slide.all_shapes() if s.image_sha1 and s.image_sha1 in approved]


def _nearest_to_box(shapes: list[ShapeModel], box: Box) -> ShapeModel:
    """The shape closest to an expected position.

    A slide with two copies of the logo should be measured against the one the
    designer meant to place, not whichever the shape tree happens to yield first.
    """
    return min(
        shapes,
        key=lambda s: abs(s.left_pt - box.left) + abs(s.top_pt - box.top),
    )


def _offset_phrase(dx: float, dy: float) -> str:
    """``18.0pt left`` / ``4.0pt right and 2.5pt down``, as a person would say it."""
    parts: list[str] = []
    if abs(dx) >= 0.05:
        parts.append(f"{abs(dx):.1f}pt {'right' if dx > 0 else 'left'}")
    if abs(dy) >= 0.05:
        parts.append(f"{abs(dy):.1f}pt {'down' if dy > 0 else 'up'}")
    return " and ".join(parts) if parts else "less than 0.1pt"


def _describe_box(left: float, top: float, width: float, height: float) -> str:
    return f"left {left:g}pt, top {top:g}pt, {width:g}x{height:g}pt"


def _shape_count(count: int) -> str:
    return "1 shape" if count == 1 else f"{count} shapes"


# --------------------------------------------------------------------------------------
# Logo
# --------------------------------------------------------------------------------------


@register
class LogoMissing(Rule):
    """Reports a slide whose archetype carries the logo in the reference deck but
    which has no shape using an approved logo image.

    Measures: for each slide whose archetype maps to a logo box in the profile,
    whether any shape's image SHA1 appears in ``brand.logo.image_sha1``.
    Archetypes mapped to ``exempt`` are not measured, and archetypes absent from
    the mapping are recorded as unchecked rather than assumed either way.

    Known false positive: identity is by content hash, so a logo that has been
    re-exported, re-compressed or swapped for a different-resolution copy of the
    same artwork hashes differently and reads as missing. That is the price of an
    identity test the user can verify; the alternative is image similarity, which
    is not deterministic enough to report as a blocker.
    """

    id: ClassVar[str] = "BR-001"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "blocker"
    summary: ClassVar[str] = "Logo absent from an archetype that requires it"
    requires: ClassVar[tuple[str, ...]] = ("brand.logo",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        logo = profile.brand.logo
        if logo is None:  # pragma: no cover - the engine skips the rule on requires
            return self.skip("no logo was learned")

        for slide in deck.slides:
            if not logo.is_known(slide.archetype):
                self.note_unchecked(
                    slide.index,
                    f"archetype {slide.archetype!r} has no learned logo expectation",
                )

        findings: list[Finding] = []
        for slide, box in _logo_targets(deck, logo):
            if _logo_shapes(slide, logo):
                continue
            findings.append(
                self.finding(
                    where=slide.index,
                    message=(
                        f"no approved logo on this {slide.archetype} slide, where the "
                        "reference deck carries one"
                    ),
                    profile=profile,
                    provenance_path="brand.logo",
                    measured="absent",
                    expected=box.describe(),
                    remedy=f"Place the logo at {box.describe()}",
                )
            )
        return findings


@register
class LogoPosition(Rule):
    """Reports a logo placed outside the box its archetype uses in the reference
    deck.

    Measures: the logo shape's stored left and top edges against
    ``brand.logo.per_archetype[archetype]``, to that box's tolerance. Size is left
    to BR-003, so a stretched logo in the right corner is reported once as a size
    defect rather than twice. Where a slide carries more than one copy of the
    logo, the copy nearest the expected position is the one measured.

    Known false positive: a deliberate one-off placement -- a logo moved to clear
    a full-width chart on a single slide -- is reported, because the profile
    records one box per archetype and has no way to express an exception.
    """

    id: ClassVar[str] = "BR-002"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "major"
    summary: ClassVar[str] = "Logo outside its archetype's expected box"
    requires: ClassVar[tuple[str, ...]] = ("brand.logo",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        logo = profile.brand.logo
        if logo is None:  # pragma: no cover - the engine skips the rule on requires
            return self.skip("no logo was learned")

        findings: list[Finding] = []
        for slide, box in _logo_targets(deck, logo):
            shapes = _logo_shapes(slide, logo)
            if not shapes:
                self.note_unchecked(
                    slide.index,
                    "no approved logo image on the slide, so its position cannot be measured",
                )
                continue

            shape = _nearest_to_box(shapes, box)
            dx = shape.left_pt - box.left
            dy = shape.top_pt - box.top
            if abs(dx) <= box.tolerance_pt and abs(dy) <= box.tolerance_pt:
                continue
            findings.append(
                self.finding(
                    where=shape.ref,
                    message=f"logo sits {_offset_phrase(dx, dy)} of its expected position",
                    profile=profile,
                    provenance_path="brand.logo.per_archetype",
                    measured=f"left {shape.left_pt:g}pt, top {shape.top_pt:g}pt",
                    expected=box.describe(),
                    remedy=f"Move the logo to left {box.left:g}pt, top {box.top:g}pt",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings


@register
class LogoSize(Rule):
    """Reports a logo scaled away from its learned size, or distorted.

    Measures two things and reports them as one finding per slide: the rendered
    width and height against ``brand.logo.per_archetype[archetype]`` with
    ``brand.logo.size_tolerance_pt``, and the rendered aspect ratio against the
    image's native pixel aspect ratio with ``brand.logo.aspect_ratio_tolerance``
    (1% by default). Distortion is measured against the native pixels rather than
    the learned box, so a logo scaled proportionally is reported as the wrong size
    and not as squashed. Where native pixel dimensions are unavailable -- EMF and
    WMF logos, which cannot be probed -- distortion is recorded as unchecked
    rather than guessed at.

    Known false positive: a logo cropped inside its picture frame renders at an
    aspect ratio that does not match the underlying image, and TieOut does not
    model the crop rectangle, so it is reported as distorted.
    """

    id: ClassVar[str] = "BR-003"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "major"
    summary: ClassVar[str] = "Logo size deviates, or its aspect ratio is distorted"
    requires: ClassVar[tuple[str, ...]] = ("brand.logo",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        logo = profile.brand.logo
        if logo is None:  # pragma: no cover - the engine skips the rule on requires
            return self.skip("no logo was learned")

        findings: list[Finding] = []
        for slide, box in _logo_targets(deck, logo):
            shapes = _logo_shapes(slide, logo)
            if not shapes:
                self.note_unchecked(
                    slide.index,
                    "no approved logo image on the slide, so its size cannot be measured",
                )
                continue

            shape = _nearest_to_box(shapes, box)
            messages = self._measure(shape, box, logo)
            if not messages:
                continue
            findings.append(
                self.finding(
                    where=shape.ref,
                    message="; ".join(messages),
                    profile=profile,
                    provenance_path="brand.logo.per_archetype",
                    measured=f"{shape.width_pt:g}x{shape.height_pt:g}pt",
                    expected=box.describe(),
                    remedy=f"Resize the logo to {box.width:g}x{box.height:g}pt",
                    bbox_pt=shape.bbox_pt,
                )
            )
        return findings

    def _measure(self, shape: ShapeModel, box: Box, logo: LogoProfile) -> list[str]:
        messages: list[str] = []
        dw = shape.width_pt - box.width
        dh = shape.height_pt - box.height
        if abs(dw) > logo.size_tolerance_pt or abs(dh) > logo.size_tolerance_pt:
            messages.append(
                f"logo is {shape.width_pt:g}x{shape.height_pt:g}pt against an expected "
                f"{box.width:g}x{box.height:g}pt "
                f"(delta {dw:+.1f}pt wide, {dh:+.1f}pt high)"
            )

        rendered = shape.aspect_ratio
        native = (
            shape.image_pixel_width / shape.image_pixel_height
            if shape.image_pixel_width and shape.image_pixel_height
            else None
        )
        if native is None or rendered is None:
            self.note_unchecked(
                shape.ref,
                "the image's native pixel size is unavailable, so aspect distortion "
                "cannot be measured",
            )
            return messages

        distortion = abs(rendered / native - 1.0)
        if distortion > logo.aspect_ratio_tolerance:
            messages.append(
                f"logo is distorted by {distortion * 100:.1f}%: rendered at "
                f"{rendered:.3f}:1 against a native {native:.3f}:1"
            )
        return messages


# --------------------------------------------------------------------------------------
# Palette and typefaces
# --------------------------------------------------------------------------------------


@register
class OffPaletteColour(Rule):
    """Reports a resolved colour that is not within Delta-E tolerance of any
    learned palette entry.

    Measures: every resolved shape fill, visible shape outline, run colour and
    table cell fill against ``brand.palette_hex``, as CIE76 Delta-E in Lab space
    with ``brand.palette_tolerance_delta_e``. Fills carrying no single measurable
    colour -- pictures, groups, explicit no-fill and unresolved inheritance -- are
    not measured. Furniture is measured: an off-brand footer is as wrong as an
    off-brand callout. Findings are clustered per slide and per offending colour,
    so one wrong swatch used six times is one finding naming six shapes.

    Not measured: colours inside a chart part -- series fills, gridlines, plot
    area -- because TieOut does not model the chart's colour scheme; and the slide
    background, which the model carries but which no learned expectation covers.

    Known false positive: a photograph's surrounding frame, a screenshot's border,
    or a neutral shade borrowed from a third party's brand in a comparison exhibit
    is reported, because the profile records one palette for the whole deck with no
    way to licence an exception.
    """

    id: ClassVar[str] = "BR-004"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "major"
    summary: ClassVar[str] = "Colour off the learned palette"
    requires: ClassVar[tuple[str, ...]] = ("brand.palette_hex",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        palette = _palette(profile)
        if not palette:
            return self.skip("no palette entry in the profile parses as an sRGB colour")

        tolerance = profile.brand.palette_tolerance_delta_e
        findings: list[Finding] = []
        for slide in deck.slides:
            for key, uses in self._offenders(slide, palette, tolerance).items():
                colour_hex, nearest_hex, delta = key
                refs = {use.ref for use in uses}
                sources = sorted({use.source for use in uses})
                findings.append(
                    self.finding(
                        where=uses[0].ref,
                        message=(
                            f"{colour_hex} is off the palette, used by "
                            f"{_shape_count(len(refs))} on this slide "
                            f"({', '.join(sources)}); the nearest palette colour "
                            f"{nearest_hex} is Delta-E {delta:.1f} away"
                        ),
                        profile=profile,
                        provenance_path="brand.palette_hex",
                        measured=f"{colour_hex}, Delta-E {delta:.1f} from {nearest_hex}",
                        expected=f"a palette colour within Delta-E {tolerance:g}",
                        remedy=f"Recolour {colour_hex} to the palette's {nearest_hex}",
                    )
                )
        return findings

    def _offenders(
        self, slide: SlideModel, palette: list[Rgb], tolerance: float
    ) -> dict[tuple[str, str, float], list[_ColourUse]]:
        """Off-palette uses on one slide, grouped by the offending colour."""
        grouped: dict[tuple[str, str, float], list[_ColourUse]] = {}
        for shape in slide.leaf_shapes():
            for use in _colour_uses(shape):
                rgb = try_parse_hex(use.hex)
                if rgb is None:
                    self.note_unchecked(
                        shape.ref,
                        f"{use.source} colour {use.hex!r} is not an sRGB triplet",
                    )
                    continue
                match = nearest(rgb, palette)
                if match is None:  # pragma: no cover - guarded by the empty check above
                    continue
                closest, delta = match
                if delta <= tolerance:
                    continue
                grouped.setdefault((rgb.hex, closest.hex, round(delta, 1)), []).append(use)
        return grouped


@register
class UnapprovedTypeface(Rule):
    """Reports a resolved typeface that is not in the learned approved set.

    Measures: the resolved name of every run in every text frame and table cell,
    plus every font resolved anywhere in a chart part, against
    ``brand.fonts.allowed``, compared case-insensitively. Runs whose text is empty
    or whitespace are not measured, because an invisible run in the wrong face
    changes nothing a reader sees. Findings are clustered per slide and per
    offending typeface.

    Known false positive: a run of symbol glyphs legitimately set in Wingdings,
    Symbol or a maths face is reported, because the resolver returns the typeface
    the run actually uses and the profile records only the text faces observed in
    the reference deck.
    """

    id: ClassVar[str] = "BR-005"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "blocker"
    summary: ClassVar[str] = "Typeface outside the approved set"
    requires: ClassVar[tuple[str, ...]] = ("brand.fonts.allowed",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        fonts = profile.brand.fonts
        expected = "one of " + ", ".join(fonts.allowed)
        # Name the typeface only when there is one to name. A house style with
        # three approved faces has no single right answer for a given run, and
        # picking one of them would be an instruction the tool cannot support.
        approved = sorted(fonts.allowed)
        remedy = (
            f"Set the typeface to {approved[0]}"
            if len(approved) == 1
            else f"Set the typeface to one of {', '.join(approved)}"
            if approved
            else None
        )

        findings: list[Finding] = []
        for slide in deck.slides:
            for name, refs, count in self._offenders(slide, profile).values():
                runs = "1 run" if count == 1 else f"{count} runs"
                findings.append(
                    self.finding(
                        where=refs[0],
                        message=(
                            f"{runs} in {_shape_count(len(refs))} set in {name}, which is "
                            "not an approved typeface"
                        ),
                        profile=profile,
                        provenance_path="brand.fonts.allowed",
                        remedy=remedy,
                        measured=name,
                        expected=expected,
                    )
                )
        return findings

    @staticmethod
    def _offenders(
        slide: SlideModel, profile: Profile
    ) -> dict[str, tuple[str, list[ShapeRef], int]]:
        """Unapproved faces on one slide, keyed by the case-folded typeface name."""
        grouped: dict[str, tuple[str, list[ShapeRef], int]] = {}
        for shape in slide.leaf_shapes():
            for name in _font_names(shape):
                if profile.brand.fonts.permits_name(name):
                    continue
                _, refs, count = grouped.get(name.casefold(), (name, [], 0))
                if shape.ref not in refs:
                    refs.append(shape.ref)
                grouped[name.casefold()] = (name, refs, count + 1)
        return grouped


# --------------------------------------------------------------------------------------
# Page numbers
# --------------------------------------------------------------------------------------


@register
class PageNumber(Rule):
    """Reports a page number that is missing, malformed, or out of position.

    Measures three conditions on every slide whose archetype appears in
    ``brand.footer.page_number.required_on``, and reports them as one finding per
    slide: no page number was found at all; the page number's text does not match
    the learned ``regex``; or its position differs from the learned ``box_pt``
    beyond that box's tolerance. Only position is compared, not size, because a
    page-number box that autofits to its digits changes width without moving the
    digits a reader sees.

    Known false positive: a slide that deliberately omits its page number -- a
    full-bleed exhibit, or a fold-out PowerPoint still counts -- is reported,
    because the requirement is recorded per archetype and cannot express a
    per-slide exception.
    """

    id: ClassVar[str] = "BR-006"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "major"
    summary: ClassVar[str] = "Page number missing, malformed or out of position"
    requires: ClassVar[tuple[str, ...]] = ("brand.footer.page_number",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        expectation = profile.brand.footer.page_number
        if expectation is None:  # pragma: no cover - the engine skips on requires
            return self.skip("no page-number expectation was learned")

        pattern: re.Pattern[str] | None = None
        try:
            pattern = re.compile(expectation.regex)
        except re.error as exc:
            self.note_unchecked(
                1, f"page-number pattern {expectation.regex!r} does not compile: {exc}"
            )

        furniture = self.furniture(deck, profile)
        box = expectation.box_pt
        findings: list[Finding] = []
        for slide in deck.slides:
            if slide.archetype not in expectation.required_on:
                continue

            observation = furniture.page_number_for(slide.index)
            if observation is None:
                findings.append(self._absent(slide, profile, expectation.regex, box))
                continue

            messages: list[str] = []
            if pattern is not None and not pattern.match(observation.text):
                messages.append(
                    f"page number {observation.text!r} does not match the learned pattern "
                    f"{expectation.regex}"
                )
            if box is not None:
                dx = observation.box_pt[0] - box.left
                dy = observation.box_pt[1] - box.top
                if abs(dx) > box.tolerance_pt or abs(dy) > box.tolerance_pt:
                    messages.append(
                        f"page number sits {_offset_phrase(dx, dy)} of its expected position"
                    )
            if not messages:
                continue
            findings.append(
                self.finding(
                    where=ShapeRef(slide.index, observation.shape_id, "Page number"),
                    message="; ".join(messages),
                    profile=profile,
                    provenance_path="brand.footer.page_number",
                    measured=(
                        f"{observation.text!r} at left {observation.box_pt[0]:g}pt, "
                        f"top {observation.box_pt[1]:g}pt"
                    ),
                    expected=box.describe() if box else f"text matching {expectation.regex}",
                    remedy=(
                        f"Move the page number to {box.describe()}"
                        if box
                        else "Correct the page number to the house pattern"
                    ),
                    bbox_pt=observation.box_pt,
                )
            )
        return findings

    def _absent(
        self, slide: SlideModel, profile: Profile, regex: str, box: Box | None
    ) -> Finding:
        """A slide with no recognised page number.

        Before reporting it as simply absent, look for text standing in the learned
        page-number position: "the page-number position holds 'Page 15 of 26'" is a
        far more useful message than "no page number", and it is the same underlying
        defect seen from the other side.
        """
        occupant = self._occupant(slide, box) if box is not None else None
        if occupant is not None:
            text = occupant.text.strip()
            return self.finding(
                where=occupant.ref,
                message=(
                    f"the page-number position holds {text!r}, which does not match the "
                    f"learned pattern {regex}"
                ),
                profile=profile,
                provenance_path="brand.footer.page_number",
                measured=repr(text),
                expected=f"text matching {regex}",
                remedy="Replace it with the slide's page number alone",
                bbox_pt=occupant.bbox_pt,
            )
        return self.finding(
            where=slide.index,
            message=(
                f"no page number on this {slide.archetype} slide, where the reference deck "
                "carries one"
            ),
            profile=profile,
            provenance_path="brand.footer.page_number",
            measured="absent",
            expected=box.describe() if box else f"text matching {regex}",
            remedy=(
                f"Add the page number at {box.describe()}"
                if box
                else "Add the page number to this slide"
            ),
        )

    @staticmethod
    def _occupant(slide: SlideModel, box: Box) -> ShapeModel | None:
        """The text shape standing in the learned page-number box, if any."""
        for shape in slide.leaf_shapes():
            if not shape.has_text or shape.table is not None:
                continue
            if approx_equal(shape.left_pt, box.left, box.tolerance_pt) and approx_equal(
                shape.top_pt, box.top, box.tolerance_pt
            ):
                return shape
        return None


@register
class PageNumberSequence(Rule):
    """Reports the first place where page numbers stop ascending.

    Measures: the integer values of the page numbers found across the deck, in
    slide order, reporting the first slide whose number is not greater than the
    previous one. Gated on ``brand.footer.page_number.must_ascend``. One finding
    per deck, on the offending slide, because a single duplicated or transposed
    number throws off every slide after it and reporting all of them describes one
    mistake many times.

    Known false positive: a deck whose appendix restarts its numbering, or which
    numbers sections independently, is reported at the restart. The profile records
    one ascending sequence for the deck and has no notion of a second one.
    """

    id: ClassVar[str] = "BR-007"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "major"
    summary: ClassVar[str] = "Page numbers not strictly ascending"
    requires: ClassVar[tuple[str, ...]] = ("brand.footer.page_number",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        expectation = profile.brand.footer.page_number
        if expectation is None:  # pragma: no cover - the engine skips on requires
            return self.skip("no page-number expectation was learned")
        if not expectation.must_ascend:
            return self.skip("the profile does not require page numbers to ascend")

        observations = sorted(
            self.furniture(deck, profile).page_numbers, key=lambda o: o.slide_index
        )
        for previous, current in pairwise(observations):
            if current.value > previous.value:
                continue
            return [
                self.finding(
                    where=ShapeRef(current.slide_index, current.shape_id, "Page number"),
                    message=(
                        f"page number {current.value} does not follow {previous.value} on "
                        f"slide {previous.slide_index}: the sequence stops ascending here"
                    ),
                    profile=profile,
                    provenance_path="brand.footer.page_number",
                    measured=str(current.value),
                    expected=f"greater than {previous.value}",
                    remedy="Renumber the slides so the sequence ascends",
                    bbox_pt=current.box_pt,
                )
            ]
        return []


# --------------------------------------------------------------------------------------
# Title geometry, canvas and boilerplate
# --------------------------------------------------------------------------------------


@register
class TitleGeometry(Rule):
    """Reports a title placeholder dragged off the position its layout gives it.

    Measures: each title placeholder's four edges against the geometry resolved
    from its layout placeholder and then its master, with
    ``brand.title_geometry_tolerance_pt`` (2pt by default) on any edge. Only
    placeholders of type ``title`` or ``ctrTitle`` are measured: a title typed into
    a plain text box has no layout placeholder to compare with, and reporting it
    would fire on every slide of the many decks built that way. Where the layout
    geometry cannot be resolved, the shape is recorded as unchecked.

    Known false positive: a deliberately repositioned title -- one slide where the
    heading is moved to clear a full-width exhibit -- is reported, because the
    layout is the only statement of intent available and the slide disagrees with
    it.
    """

    id: ClassVar[str] = "BR-008"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "minor"
    summary: ClassVar[str] = "Title geometry drifts from its layout placeholder"
    requires: ClassVar[tuple[str, ...]] = ("brand.title_geometry_tolerance_pt",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        tolerance = profile.brand.title_geometry_tolerance_pt
        findings: list[Finding] = []
        for slide in deck.slides:
            for shape in slide.all_shapes():
                if shape.placeholder_type not in ("title", "ctrTitle"):
                    continue
                if shape.layout_geometry_pt is None:
                    self.note_unchecked(
                        shape.ref,
                        "the layout placeholder's geometry could not be resolved, so "
                        "title drift cannot be measured",
                    )
                    continue
                finding = self._measure(shape, shape.layout_geometry_pt, tolerance, profile)
                if finding is not None:
                    findings.append(finding)
        return findings

    def _measure(
        self,
        shape: ShapeModel,
        layout: tuple[float, float, float, float],
        tolerance: float,
        profile: Profile,
    ) -> Finding | None:
        left, top, width, height = layout
        edges = (
            ("left", shape.left_pt - left),
            ("top", shape.top_pt - top),
            ("right", shape.right_pt - (left + width)),
            ("bottom", shape.bottom_pt - (top + height)),
        )
        worst_edge, worst_delta = max(edges, key=lambda pair: abs(pair[1]))
        if abs(worst_delta) <= tolerance:
            return None
        breached = ", ".join(
            f"{name} {delta:+.1f}pt" for name, delta in edges if abs(delta) > tolerance
        )
        return self.finding(
            where=shape.ref,
            message=(
                f"title placeholder is {abs(worst_delta):.1f}pt off its layout position on "
                f"the {worst_edge} edge ({breached})"
            ),
            profile=profile,
            provenance_path="brand.title_geometry_tolerance_pt",
            measured=_describe_box(*shape.bbox_pt),
            expected=f"{_describe_box(left, top, width, height)} (+/-{tolerance:g}pt)",
            remedy="Reset the title placeholder to its layout position",
            bbox_pt=shape.bbox_pt,
        )


@register
class SlideDimensions(Rule):
    """Reports a deck whose canvas is not the size the profile records.

    Measures: ``deck.width_pt`` and ``deck.height_pt`` against ``slide.width_pt``
    and ``slide.height_pt`` with ``slide.tolerance_pt``. One finding for the deck,
    placed on slide 1, because the canvas is a property of the file and repeating
    it on every slide would bury everything else.

    Known false positive: none within a deck -- slide dimensions are invariant, and
    a mismatch is always a real defect, usually a 16:9 deck pasted into a 4:3
    template, which re-flows every slide in it. The finding does depend on the
    reference deck having been the intended size: a profile learned from a legacy
    4:3 deck reports every current one.
    """

    id: ClassVar[str] = "BR-009"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "blocker"
    summary: ClassVar[str] = "Slide dimensions do not match the profile"
    requires: ClassVar[tuple[str, ...]] = ("slide",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        expected = profile.slide
        tolerance = expected.tolerance_pt
        if approx_equal(deck.width_pt, expected.width_pt, tolerance) and approx_equal(
            deck.height_pt, expected.height_pt, tolerance
        ):
            return []
        return [
            self.finding(
                where=1,
                message=(
                    f"the deck canvas is {deck.width_pt:g}x{deck.height_pt:g}pt against an "
                    f"expected {expected.width_pt:g}x{expected.height_pt:g}pt, so every "
                    "learned position in the profile is measured against the wrong canvas"
                ),
                profile=profile,
                provenance_path="slide",
                measured=f"{deck.width_pt:g}x{deck.height_pt:g}pt",
                remedy=(
                    f"Set the slide size to "
                    f"{expected.width_pt:g}x{expected.height_pt:g}pt (Design > Slide Size)"
                ),
                expected=(
                    f"{expected.width_pt:g}x{expected.height_pt:g}pt (+/-{tolerance:g}pt)"
                ),
            )
        ]


@register
class MissingBoilerplate(Rule):
    """Reports required boilerplate absent from a slide whose archetype requires it.

    Measures: for each entry in ``brand.footer.boilerplate``, every slide whose
    archetype appears in that entry's ``required_on``, testing whether any shape's
    text contains the expected string. Matching honours the entry's
    ``match_normalised``, which normalises case, whitespace and quote style,
    because a footer retyped by hand differs from the original in ways no reader
    would notice. One finding per slide per missing entry.

    Known false positive: boilerplate placed on the layout or the master rather
    than on the slide is reported as missing, because the model carries slide
    shapes and a reader cannot tell the difference. A deck built that way reports
    on every slide, which is the signal to disable the rule rather than to accept
    each finding.
    """

    id: ClassVar[str] = "BR-010"
    category: ClassVar[str] = "brand"
    severity: ClassVar[Severity] = "major"
    summary: ClassVar[str] = "Required boilerplate text absent"
    requires: ClassVar[tuple[str, ...]] = ("brand.footer.boilerplate",)

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for entry in profile.brand.footer.boilerplate:
            expected = (
                normalise_text(entry.text) if entry.match_normalised else entry.text.strip()
            )
            if not expected:
                continue
            label = "confidentiality marking" if entry.is_confidentiality else "footer text"
            for slide in deck.slides:
                if slide.archetype not in entry.required_on:
                    continue
                if self._present(slide, expected, normalised=entry.match_normalised):
                    continue
                findings.append(
                    self.finding(
                        where=slide.index,
                        message=(
                            f"the required {label} {entry.text!r} is absent from this "
                            f"{slide.archetype} slide"
                        ),
                        profile=profile,
                        provenance_path="brand.footer.boilerplate",
                        measured="absent",
                        expected=entry.text,
                        remedy=f"Add the {label}: {entry.text!r}",
                    )
                )
        return findings

    @staticmethod
    def _present(slide: SlideModel, expected: str, *, normalised: bool) -> bool:
        for shape in slide.all_shapes():
            if not shape.has_text:
                continue
            haystack = normalise_text(shape.text) if normalised else shape.text
            if expected in haystack:
                return True
        return False
