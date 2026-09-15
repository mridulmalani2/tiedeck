"""Hygiene rules.

Hygiene is the category that stops an internal working draft leaving the
building. Nothing here is a matter of taste: a speaker note, a comment, an
author name in ``docProps`` or a link to ``C:\\Users\\analyst\\Desktop`` is
either present or it is not, and every one of them has embarrassed a bank in
front of a client at least once.

Three of these rules (HY-004, HY-005, HY-007) read the package layer --
:class:`tieout.model.package.PackageInfo`, which is the zip archive, not
python-pptx -- because the things they look for are not in the slide parts at
all. The remaining six read :class:`~tieout.model.deck.DeckModel`.

The hygiene expectations are almost all booleans with a safe default rather than
values learned from a reference deck, so their provenance legitimately reads
"default, not inferred". That is deliberate: a user must be able to tell a rule
measuring their own house style from a rule measuring ours.
"""

from __future__ import annotations

import re
from typing import ClassVar, Final

from tieout.model.deck import DeckModel, ShapeModel, SlideModel
from tieout.model.package import Relationship
from tieout.model.units import pt_to_inches
from tieout.profile.schema import Confidence, Profile, Severity
from tieout.rules.base import Finding, Rule, register

# --------------------------------------------------------------------------------------
# Standard system fonts (HY-009)
# --------------------------------------------------------------------------------------

#: Typefaces present on essentially every Windows or Office install, so a deck
#: using one renders as intended on the client's machine without being embedded.
#:
#: This list answers one question only -- "will this resolve?" -- and says
#: nothing about whether the typeface is on brand. Comic Sans MS is on it because
#: it does ship with Office; BR-005 is the rule that objects to it.
#:
#: Matching is case-insensitive and ignores a trailing weight or width suffix, so
#: "Segoe UI Semibold" and "Arial Narrow" resolve to entries below.
STANDARD_SYSTEM_FONTS: Final[frozenset[str]] = frozenset(
    {
        # Office core and ClearType families.
        "Aptos",
        "Arial",
        "Calibri",
        "Cambria",
        "Cambria Math",
        "Candara",
        "Consolas",
        "Constantia",
        "Corbel",
        "Courier",
        "Courier New",
        "Franklin Gothic",
        "Franklin Gothic Medium",
        "Garamond",
        "Georgia",
        "Gill Sans MT",
        "Helvetica",
        "Impact",
        "Lucida Console",
        "Lucida Sans",
        "Lucida Sans Unicode",
        "Palatino Linotype",
        "Rockwell",
        "Segoe UI",
        "Segoe UI Emoji",
        "Segoe UI Symbol",
        "Tahoma",
        "Times New Roman",
        "Trebuchet MS",
        "Verdana",
        # Symbol faces, which carry no weight suffix and must match exactly.
        "Marlett",
        "Symbol",
        "Webdings",
        "Wingdings",
        "Wingdings 2",
        "Wingdings 3",
        # Other faces shipped with Windows or Office for long enough to rely on.
        "Book Antiqua",
        "Bookman Old Style",
        "Century Gothic",
        "Century Schoolbook",
        "Comic Sans MS",
        "MS PGothic",
        "Perpetua",
        "Sylfaen",
    }
)

#: Trailing words naming a weight, width or slope rather than a family. They are
#: stripped one at a time, so "Segoe UI Semibold Italic" reduces to "Segoe UI".
_WEIGHT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "black",
        "bold",
        "book",
        "condensed",
        "demi",
        "demibold",
        "extrabold",
        "extralight",
        "heavy",
        "italic",
        "light",
        "medium",
        "narrow",
        "oblique",
        "regular",
        "semibold",
        "semilight",
        "thin",
        "ultrabold",
        "ultralight",
    }
)

_STANDARD_FOLDED: Final[frozenset[str]] = frozenset(
    name.casefold() for name in STANDARD_SYSTEM_FONTS
)

#: ``ppt/slides/slide7.xml`` -> 7, for mapping a relationship back to a slide.
_SLIDE_PART_RE: Final[re.Pattern[str]] = re.compile(r"^ppt/slides/slide(\d+)\.xml$")

#: Placeholder types whose emptiness is normal rather than an unfinished slide.
#: PowerPoint inserts date, footer and slide-number placeholders from the layout
#: and leaves them blank unless the deck actually uses that furniture.
_FURNITURE_PLACEHOLDERS: Final[frozenset[str]] = frozenset({"dt", "ftr", "sldNum"})


def _font_base_name(typeface: str) -> str:
    """Fold a typeface name to its family, dropping trailing weight words."""
    words = typeface.strip().casefold().split()
    while len(words) > 1 and words[-1] in _WEIGHT_SUFFIXES:
        words.pop()
    return " ".join(words)


def _compile_marker(marker: str) -> re.Pattern[str] | None:
    """Compile a placeholder marker, or return None if it must match literally.

    Alphabetic markers get word boundaries, because a bare substring search for
    "TK" matches "stockholders" and "INSERT" matches "inserted". Symbolic markers
    such as ``[ ]`` and ``??`` have no word boundary to anchor to and are matched
    literally instead. Internal whitespace is relaxed to ``\\s+`` so "lorem
    ipsum" broken across a line still matches.
    """
    if not re.fullmatch(r"[\w\s'\u2019-]+", marker):
        return None
    pattern = re.escape(marker.strip()).replace(r"\ ", r"\s+")
    return re.compile(rf"\b{pattern}\b", re.IGNORECASE)


def _slide_index_of_part(part_name: str) -> int | None:
    match = _SLIDE_PART_RE.match(part_name)
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------------------
# HY-001
# --------------------------------------------------------------------------------------


@register
class PlaceholderMarkerRule(Rule):
    """Placeholder or draft marker text left anywhere in the deck.

    Measures: every marker in ``hygiene.placeholder_markers``, searched against
    each slide's shape text, table cell text, chart text strings and speaker
    notes. Alphabetic markers ("TBD", "INSERT") match as whole words,
    case-insensitively; symbolic markers ("[ ]", "??") match as literal
    substrings. One finding per slide, naming every distinct marker found on it.

    False-positive mode: a marker that is also ordinary prose. "TK" is a valid
    ticker and airline code, "??" appears in quoted dialogue, and a client whose
    house style writes "[ ]" for a deliberate blank in a table will see this fire
    on every such table. Narrow ``hygiene.placeholder_markers`` rather than
    disabling the rule -- it is the only check that catches an unfinished deck.
    """

    id: ClassVar[str] = "HY-001"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "blocker"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Placeholder or draft marker text left in the deck"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        markers = [m for m in profile.hygiene.placeholder_markers if m.strip()]
        if not markers:
            return self.skip("the profile lists no placeholder markers")

        compiled = [(marker, _compile_marker(marker)) for marker in markers]
        findings: list[Finding] = []
        for slide in deck.slides:
            found = self._markers_on_slide(slide, compiled)
            if not found:
                continue
            listed = ", ".join(found)
            findings.append(
                self.finding(
                    where=slide.index,
                    message=f"placeholder or draft marker left in the text: {listed}",
                    profile=profile,
                    provenance_path="hygiene.placeholder_markers",
                    measured=listed,
                    expected="no placeholder markers",
                    remedy="Replace the placeholder text with the final wording",
                )
            )
        return findings

    @staticmethod
    def _markers_on_slide(
        slide: SlideModel, compiled: list[tuple[str, re.Pattern[str] | None]]
    ) -> list[str]:
        """Distinct markers found on one slide, in profile order.

        ``ShapeModel.text`` already folds in table cells and chart text strings,
        so leaf shapes plus the notes text cover everything a reader can see.
        """
        haystacks = [shape.text for shape in slide.leaf_shapes() if shape.text]
        if slide.notes_text.strip():
            haystacks.append(slide.notes_text)

        found: list[str] = []
        for marker, pattern in compiled:
            needle = marker.strip().casefold()
            hit = any(
                pattern.search(text) if pattern is not None else needle in text.casefold()
                for text in haystacks
            )
            if hit:
                found.append(marker)
        return found


# --------------------------------------------------------------------------------------
# HY-002
# --------------------------------------------------------------------------------------


@register
class SpeakerNotesRule(Rule):
    """Speaker notes left on a slide when the client does not allow them.

    Measures: ``SlideModel.notes_text`` stripped of whitespace, on every slide,
    when ``hygiene.allow_speaker_notes`` is false. One finding per slide,
    reporting the note's length rather than its text, because the note itself may
    be the sensitive content.

    False-positive mode: a deck genuinely delivered with a speaker script, where
    the notes are the deliverable rather than a leftover. Set
    ``hygiene.allow_speaker_notes`` for that client instead of accepting the
    findings one by one.
    """

    id: ClassVar[str] = "HY-002"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "major"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Speaker notes present when the profile disallows them"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        if profile.hygiene.allow_speaker_notes:
            return self.skip("the profile allows speaker notes")

        findings: list[Finding] = []
        for slide in deck.slides:
            notes = slide.notes_text.strip()
            if not notes:
                continue
            findings.append(
                self.finding(
                    where=slide.index,
                    message=f"speaker notes left on the slide ({len(notes)} characters)",
                    profile=profile,
                    provenance_path="hygiene.allow_speaker_notes",
                    measured=f"{len(notes)} characters of notes",
                    expected="no speaker notes",
                    remedy="Delete the speaker notes before sending",
                )
            )
        return findings


# --------------------------------------------------------------------------------------
# HY-003
# --------------------------------------------------------------------------------------


@register
class HiddenSlideRule(Rule):
    """Hidden slides left in the deck when the client does not allow them.

    Measures: ``DeckModel.hidden_slide_indices`` unioned with each slide's
    ``is_hidden`` flag -- the ``show="0"`` attribute on ``p:sld`` -- when
    ``hygiene.allow_hidden_slides`` is false. One finding for the deck, listing
    every hidden slide, because hiding a slide is a decision about the deck
    rather than about one page.

    False-positive mode: a backup or appendix slide deliberately hidden for the
    live meeting and intended to travel with the file. The rule cannot tell that
    from a draft slide someone hid instead of deleting, which is why it reports
    rather than guesses.
    """

    id: ClassVar[str] = "HY-003"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "major"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Hidden slides present when the profile disallows them"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        if profile.hygiene.allow_hidden_slides:
            return self.skip("the profile allows hidden slides")

        hidden = sorted(
            set(deck.hidden_slide_indices)
            | {slide.index for slide in deck.slides if slide.is_hidden}
        )
        if not hidden:
            return []

        listed = ", ".join(str(index) for index in hidden)
        plural = "s" if len(hidden) > 1 else ""
        return [
            self.finding(
                where=hidden[0],
                message=f"hidden slide{plural} left in the deck: {listed}",
                profile=profile,
                provenance_path="hygiene.allow_hidden_slides",
                measured=f"{len(hidden)} hidden slide{plural} ({listed})",
                expected="no hidden slides",
                remedy="Delete the hidden slides, or unhide them deliberately",
            )
        ]


# --------------------------------------------------------------------------------------
# HY-004
# --------------------------------------------------------------------------------------


@register
class DocumentMetadataRule(Rule):
    """Identifying document metadata left in ``docProps``.

    Measures: ``CoreProperties.identifying_fields()`` over ``docProps/core.xml``
    (creator, lastModifiedBy, category, keywords) and
    ``AppProperties.identifying_fields()`` over ``docProps/app.xml`` (company,
    manager), both read straight from the zip archive because python-pptx does
    not expose all of them. Empty and whitespace-only fields are not reported.
    One finding for the deck, listing every leaking field as ``field=value``.

    False-positive mode: a client whose own name is the legitimate value of
    ``company`` on a deck they authored themselves. The rule cannot distinguish
    the bank's analyst from the client's own administrator, so a deck circulated
    internally will report metadata that is not in fact a leak.
    ``hygiene.allow_document_metadata`` turns the whole check off.
    """

    id: ClassVar[str] = "HY-004"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "blocker"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Author or company metadata left in docProps"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        if profile.hygiene.allow_document_metadata:
            return self.skip("the profile allows document metadata")

        package = deck.package
        leaking: dict[str, str] = {
            **package.core.identifying_fields(),
            **package.app.identifying_fields(),
        }
        if not leaking:
            return []

        rendered = ", ".join(f"{name}={value}" for name, value in leaking.items())
        return [
            self.finding(
                where=1,
                message=(
                    "document metadata identifies a person or an internal project: "
                    f"{rendered}"
                ),
                profile=profile,
                provenance_path="hygiene.allow_document_metadata",
                measured=rendered,
                expected="docProps carries no identifying fields",
                remedy=(
                    "Clear the document properties: File > Info > "
                    "Check for Issues > Inspect Document"
                ),
            )
        ]


# --------------------------------------------------------------------------------------
# HY-005
# --------------------------------------------------------------------------------------


@register
class CommentsRule(Rule):
    """PowerPoint comments left in the package.

    Measures: ``PackageInfo.has_comments``, ``total_comment_count`` and
    ``comment_authors``, derived from every ``ppt/comments/*.xml`` part in the
    archive. The package layer reads both the legacy ``p:cm`` shape and the
    modern ``p188:cm`` shape, so a comment written by any PowerPoint version
    since 2016 is counted. One finding for the deck, because "this file carries
    reviewer comments" is one fact however many comments there are.

    False-positive mode: none that produces a wrong finding -- a comment part
    with a positive count is a comment. The rule is silent, however, on a comment
    PowerPoint has deleted while leaving an empty part behind, which is a false
    negative rather than a false positive.
    """

    id: ClassVar[str] = "HY-005"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "blocker"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "PowerPoint comments present in the package"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        if profile.hygiene.allow_comments:
            return self.skip("the profile allows comments")

        package = deck.package
        if not package.has_comments:
            return []

        count = package.total_comment_count
        authors = package.comment_authors
        plural = "s" if count != 1 else ""
        attribution = f" by {', '.join(authors)}" if authors else ""
        return [
            self.finding(
                where=1,
                message=(
                    f"{count} PowerPoint comment{plural}{attribution} left in the package"
                ),
                profile=profile,
                provenance_path="hygiene.allow_comments",
                measured=f"{count} comment{plural}{attribution}",
                expected="no comments",
                remedy="Delete every comment before sending (Review > Delete > All)",
            )
        ]


# --------------------------------------------------------------------------------------
# HY-006
# --------------------------------------------------------------------------------------


@register
class EmptyPlaceholderRule(Rule):
    """An empty placeholder left visible on a slide.

    Measures: ``ShapeModel.is_empty_placeholder`` -- a placeholder with no text
    and no picture, table or chart content -- across every shape on every slide.
    One finding per slide.

    Two exclusions, both of which would otherwise dominate the output. A
    placeholder with zero width or height cannot be seen and is not a defect. A
    date, footer or slide-number placeholder (``dt``, ``ftr``, ``sldNum``) is
    inherited from the layout and is routinely left blank by decks that do not
    use that furniture.

    False-positive mode: a placeholder deliberately left empty as white space in
    a designed layout, and a placeholder whose only content is a shape TieOut
    does not model -- SmartArt in particular, whose geometry lives in a diagram
    part the model deliberately does not read, so a SmartArt-filled placeholder
    can look empty here.
    """

    id: ClassVar[str] = "HY-006"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "major"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Empty placeholder left visible on a slide"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        findings: list[Finding] = []
        for slide in deck.slides:
            offenders = [
                shape
                for shape in slide.all_shapes()
                if self._is_visible_and_empty(shape)
            ]
            if offenders:
                findings.append(self._report(slide, offenders, profile))
        return findings

    @staticmethod
    def _is_visible_and_empty(shape: ShapeModel) -> bool:
        if not shape.is_empty_placeholder:
            return False
        if shape.width_pt <= 0 or shape.height_pt <= 0:
            return False
        return (shape.placeholder_type or "") not in _FURNITURE_PLACEHOLDERS

    def _report(
        self, slide: SlideModel, offenders: list[ShapeModel], profile: Profile
    ) -> Finding:
        """One finding per slide, pointing at the shape when there is only one.

        With a single offender the shape reference and its box are the actionable
        detail; with several, naming the slide and listing them is more honest
        than arbitrarily picking one to carry the finding.
        """
        if len(offenders) == 1:
            only = offenders[0]
            return self.finding(
                where=only.ref,
                message=f"empty placeholder left visible: {only.ref.display_name}",
                profile=profile,
                provenance_path="hygiene",
                measured="no text, picture, table or chart content",
                expected="the placeholder is filled or removed",
                remedy="Fill the placeholder, or delete it",
                bbox_pt=only.bbox_pt,
            )
        names = ", ".join(shape.ref.display_name for shape in offenders)
        return self.finding(
            where=slide.index,
            message=f"{len(offenders)} empty placeholders left visible: {names}",
            profile=profile,
            provenance_path="hygiene",
            measured=names,
            expected="every placeholder is filled or removed",
            remedy="Fill each placeholder, or delete it",
        )


# --------------------------------------------------------------------------------------
# HY-007
# --------------------------------------------------------------------------------------


@register
class ExternalRelationshipRule(Rule):
    """An external or broken relationship in the package.

    Measures: ``PackageInfo.suspect_relationships()`` -- every ``_rels/*.rels``
    entry with ``TargetMode="External"`` whose target is a ``file://`` URL, a
    Windows drive letter or a UNC path, plus any linked rather than embedded OLE
    object, image, audio, video or media payload -- together with
    ``broken_internal_relationships()``, whose target part is absent from the
    archive. An ordinary ``https://`` hyperlink is not reported. Each finding
    lands on the slide owning the relationship, resolved from its ``source_part``
    and, for a chart or embedding, from the slide that references that part;
    slide 1 is the fallback when the owner cannot be established.

    False-positive mode: a linked payload genuinely intended to stay linked, on a
    deck that will only ever be opened on the machine holding the target. The
    rule also cannot tell a deliberately shared network path from a personal
    desktop path -- both break for the recipient, but only one was a mistake.
    """

    id: ClassVar[str] = "HY-007"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "blocker"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "External or broken relationship in the package"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        if profile.hygiene.allow_external_relationships:
            return self.skip("the profile allows external relationships")

        owners = _relationship_slide_owners(deck)
        findings = [
            self._report(
                rel,
                owners,
                profile,
                kind="external link",
                detail=f"external target {rel.target}",
            )
            for rel in deck.package.suspect_relationships()
        ]
        findings.extend(
            self._report(
                rel,
                owners,
                profile,
                kind="broken link",
                detail=f"missing target part {rel.target}",
            )
            for rel in _really_broken(deck)
        )
        return findings

    def _report(
        self,
        rel: Relationship,
        owners: dict[str, int],
        profile: Profile,
        *,
        kind: str,
        detail: str,
    ) -> Finding:
        return self.finding(
            where=owners.get(rel.source_part, 1),
            message=f"{kind} in {rel.source_part}: {rel.short_type} -> {rel.target}",
            profile=profile,
            provenance_path="hygiene.allow_external_relationships",
            measured=f"{rel.short_type} relationship, {detail}",
            expected="every payload embedded and every target present",
            remedy="Embed the linked content, or remove the link",
        )


def _relationship_slide_owners(deck: DeckModel) -> dict[str, int]:
    """Source part name -> the slide index a finding about it should land on.

    Slide parts map directly. A chart or embedded-object part is walked back one
    level through the internal relationships referencing it, so an external link
    hidden inside ``ppt/charts/chart2.xml`` still lands on the slide the reader
    has to fix rather than on slide 1.
    """
    owners: dict[str, int] = {}
    for name in deck.package.part_names:
        index = _slide_index_of_part(name)
        if index is not None:
            owners[name] = index

    for rel in deck.package.relationships:
        if rel.is_external:
            continue
        source_index = owners.get(rel.source_part)
        if source_index is None:
            continue
        target = _resolve_target(rel)
        if target and target not in owners:
            owners[target] = source_index
    return owners


def _resolve_target(rel: Relationship) -> str | None:
    """Resolve an internal relationship target to a package part name.

    Duplicates ``tieout.model.package._resolve_part``, which is private, and
    additionally drops the leading slash that helper leaves on a target declared
    from the package root.
    """
    target = rel.target
    if not target or target.startswith(("http://", "https://", "mailto:", "file:")):
        return None
    base = rel.source_part.rsplit("/", 1)[0] if "/" in rel.source_part else ""
    joined = target if target.startswith("/") or not base else f"{base}/{target}"
    parts: list[str] = []
    for segment in joined.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts)


def _really_broken(deck: DeckModel) -> list[Relationship]:
    """Broken internal relationships, minus the package layer's false positives.

    ``PackageInfo.broken_internal_relationships`` reports the four relationships
    in the root ``_rels/.rels`` on every well-formed deck: their source part
    resolves to ``"/"``, so their targets resolve to ``/ppt/...`` with a leading
    slash no part name carries. Re-resolving each candidate here and discarding
    the ones whose target is in fact present keeps HY-007 silent on a clean deck
    without reimplementing the walk.
    """
    known = set(deck.package.part_names)
    return [
        rel
        for rel in deck.package.broken_internal_relationships()
        if (resolved := _resolve_target(rel)) is not None and resolved not in known
    ]


# --------------------------------------------------------------------------------------
# HY-008
# --------------------------------------------------------------------------------------


@register
class ImageResolutionRule(Rule):
    """An image whose effective resolution is below the client's floor.

    Measures: for every shape carrying an image, the media part's native pixel
    width divided by the width the shape renders at in inches, compared against
    ``hygiene.min_image_dpi``. One finding per image: images are few enough per
    deck that clustering them by slide would cost the identity of the offender
    for no gain.

    Records as unchecked, rather than passing, any image whose effective DPI
    cannot be computed: EMF and WMF are vector formats with no pixel dimensions,
    and a shape of zero width has no rendered size to divide by.

    False-positive mode: an image deliberately used at low resolution -- a
    scanned signature, a screenshot of a low-resolution interface, a decorative
    texture -- and an image cropped inside PowerPoint, where the native pixel
    width covers the whole source and so overstates the pixels actually landing
    in the visible box.
    """

    id: ClassVar[str] = "HY-008"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "major"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Image effective resolution below the profile's floor"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        floor = profile.hygiene.min_image_dpi
        findings: list[Finding] = []
        for slide in deck.slides:
            for shape in slide.all_shapes():
                # Any shape with a media payload, not only kind == "picture", so
                # a filled picture placeholder is measured too.
                if shape.image_sha1 is None:
                    continue
                finding = self._check(shape, shape.image_sha1, deck, profile, floor)
                if finding is not None:
                    findings.append(finding)
        return findings

    def _check(
        self,
        shape: ShapeModel,
        sha1: str,
        deck: DeckModel,
        profile: Profile,
        floor: float,
    ) -> Finding | None:
        media = deck.package.media_by_sha1(sha1)
        if media is None:
            self.note_unchecked(
                shape.ref, "the image's media part is not present in the package"
            )
            return None

        rendered_inches = pt_to_inches(shape.width_pt)
        if rendered_inches is None or rendered_inches <= 0:
            self.note_unchecked(shape.ref, "the shape has no rendered width")
            return None

        dpi = media.effective_dpi(rendered_inches)
        if dpi is None:
            self.note_unchecked(
                shape.ref,
                f"{media.format or 'vector'} media has no pixel dimensions, so "
                "effective DPI is undefined",
            )
            return None
        if dpi >= floor:
            return None

        return self.finding(
            where=shape.ref,
            message=(
                f"image renders at {dpi:.0f} effective DPI, below the {floor:g} DPI floor"
            ),
            profile=profile,
            provenance_path="hygiene.min_image_dpi",
            measured=(
                f"{dpi:.0f} DPI ({media.pixel_width}px across {rendered_inches:.2f}in)"
            ),
            expected=f"at least {floor:g} DPI",
            remedy=f"Replace the image with one of at least {floor:g} DPI at this size",
            bbox_pt=shape.bbox_pt,
        )


# --------------------------------------------------------------------------------------
# HY-009
# --------------------------------------------------------------------------------------


@register
class NonStandardFontRule(Rule):
    """A typeface that is neither embedded nor a standard system font.

    Measures: every typeface in ``DeckModel.fonts_used`` against three sources of
    acceptability -- the package's ``embedded_typefaces``, the bundled
    :data:`STANDARD_SYSTEM_FONTS` list, and ``hygiene.extra_standard_fonts``.
    Matching is case-insensitive and ignores a trailing weight or width suffix,
    so "Segoe UI Semibold" is accepted on the strength of "Segoe UI". One finding
    per offending typeface, reported against the deck with the number of
    characters set in it, because the font is a property of the deck rather than
    of any one slide.

    This asks only whether the typeface will resolve on the recipient's machine.
    Whether it is on brand is BR-005's question, and a font can fail one check
    while passing the other.

    Records as unchecked any shape carrying a run whose typeface could not be
    resolved at all, since an unresolved name is not evidence of a missing font.

    False-positive mode: a font that is standard on the client's managed estate
    but absent from the bundled list -- a licensed corporate typeface, or a
    non-Windows system face. ``hygiene.extra_standard_fonts`` is the fix, and is
    why this rule is a minor rather than a blocker.
    """

    id: ClassVar[str] = "HY-009"
    category: ClassVar[str] = "hygiene"
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "medium"
    default_enabled: ClassVar[bool] = True
    summary: ClassVar[str] = "Font neither embedded nor a standard system font"
    requires: ClassVar[tuple[str, ...]] = ()

    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        self._note_unresolved_runs(deck)
        acceptable = self._acceptable_names(deck, profile)

        findings: list[Finding] = []
        for typeface, char_count in sorted(deck.fonts_used.items()):
            if typeface.casefold() in acceptable:
                continue
            if _font_base_name(typeface) in acceptable:
                continue
            findings.append(
                self.finding(
                    where=1,
                    message=(
                        f"{typeface!r} is neither embedded nor a standard system font, "
                        "so it will be substituted on the recipient's machine"
                    ),
                    profile=profile,
                    provenance_path="hygiene.extra_standard_fonts",
                    measured=f"{typeface} set in {char_count} characters",
                    expected="an embedded or standard system font",
                    remedy=f"Embed {typeface}, or set the text in a standard system font",
                )
            )
        return findings

    @staticmethod
    def _acceptable_names(deck: DeckModel, profile: Profile) -> set[str]:
        """Folded names and family names that need no substitution."""
        acceptable = set(_STANDARD_FOLDED)
        declared = list(deck.package.embedded_typefaces) + list(
            profile.hygiene.extra_standard_fonts
        )
        for typeface in declared:
            acceptable.add(typeface.casefold())
            acceptable.add(_font_base_name(typeface))
        return acceptable

    def _note_unresolved_runs(self, deck: DeckModel) -> None:
        """Flag shapes whose typeface could not be resolved.

        ``DeckModel.fonts_used`` silently drops runs with no resolved name, so
        without this the rule would report a clean pass on text whose font is
        simply unknown.
        """
        for slide in deck.slides:
            for shape in slide.leaf_shapes():
                unresolved = any(
                    run.font.name is None and run.text.strip()
                    for paragraph in shape.all_paragraphs
                    for run in paragraph.runs
                )
                if unresolved:
                    self.note_unchecked(
                        shape.ref,
                        "the typeface of at least one run could not be resolved",
                    )
