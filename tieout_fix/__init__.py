"""Applying the corrections TieOut reports, where they can be applied exactly.

A fourth top-level package, for the same reason ``tieout_review`` is a separate
one: the core promises never to write to a deck, and a module inside it that did
would make that promise a matter of reading the code rather than of its shape.
Nothing in ``tieout`` imports this.

**What may be fixed, and what may never be.**

A fix is offered only where the change is a substitution the tool can make
without deciding anything: a colour to the palette colour it is nearest, a
typeface to the approved one, a variant spelling to the canon term, a double
space to one space, a document property to nothing at all. Each has exactly one
correct outcome, and applying it cannot make the deck worse.

Geometry is never fixed *by the tool*, and that is a finding rather than a
policy. Snapping the six shapes LO-003 reported on a real deck to their nearest
grid line was tried: it drove one text box into its neighbour -- a *major*
overlap where there had been none -- and turned a timetable column spaced evenly
to the point into one varying by five. The grid lines were derived from that
deck, different shapes align to different ones, and pulling a group onto a line
breaks its relationship with everything around it. Which alignment matters is a
judgement about what the slide is for. So LO-*, the logo rules and BR-008 state
the measurement and stop, and no builder below produces a correction for them.

Nor is anything fixed where the tool can see a problem but not the answer. Two
figures that disagree, a total that does not sum, a placeholder that needs real
words, a word the dictionary does not know, text that overflows its box: the
tool knows something is wrong and has no way to know what is right. Inventing a
value there would be worse than silence, because it would be wrong invisibly.

**The one exception, and why it is not one.** :func:`move_fix` writes geometry.
It is not a correction TieOut decided on: there is no builder for it, no rule
produces it, and :func:`plan_fixes` can never return one. It exists only to
carry two numbers a person produced with their own mouse or arrow keys onto the
shape they were looking at. The distinction the rest of this module rests on is
between a value the tool derived and a value the tool was handed, and a move is
the second kind -- so the refusal above is intact. What TieOut still will not do
is decide *where* a shape belongs, which is the judgement the grid-snapping
experiment got wrong.

Everything this package declines to fix is still reported. The deck a person
gets back is the deck they gave, with the mechanical corrections made and every
judgement still theirs.
"""

from __future__ import annotations

import posixpath
import re
import shutil
import zipfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from lxml import etree

from tieout.model.units import EMU_PER_POINT
from tieout.profile.schema import Profile
from tieout.rules.base import SEVERITY_ORDER, AuditResult, Finding

__all__ = [
    "MOVE_KIND",
    "MOVE_RULE_ID",
    "Changed",
    "Delta",
    "Fix",
    "FixReport",
    "MoveFailed",
    "action_key",
    "apply_fix",
    "delta",
    "finding_key",
    "move_fix",
    "move_key",
    "plan_fixes",
]

#: Parts whose XML carries slide content. Colour and typeface substitutions walk
#: these; charts and diagrams are included because a series recoloured by hand
#: and a SmartArt label in the wrong face are both things BR-004 and BR-005
#: report.
_CONTENT_PARTS: Final[tuple[str, ...]] = (
    "ppt/slides/slide",
    "ppt/charts/chart",
    "ppt/diagrams/data",
    "ppt/notesSlides/notesSlide",
)


@dataclass(frozen=True, slots=True)
class Fix:
    """One correction, and enough to apply it without re-deriving anything.

    ``key`` matches the review note's action, because an action is already the
    unit a person decides about: "recolour this gold to the palette's grey" is
    one decision whether it appears on one slide or four.
    """

    key: str
    rule_id: str
    #: The imperative sentence the note already shows.
    summary: str
    slides: tuple[int, ...]
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class FixReport:
    """What applying a fix actually changed."""

    key: str
    applied: bool
    changes: int = 0
    detail: str = ""


class MoveFailed(Exception):
    """The shape a move names is not where the move says it is.

    Its own exception because every other way a fix can fail is a defect in the
    package, while this one usually means the deck has changed under a page still
    showing the previous audit -- a different thing to tell someone, and a
    recoverable one.
    """


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------


def plan_fixes(result: AuditResult, profile: Profile) -> dict[str, Fix]:
    """Every finding that can be corrected exactly, keyed by its action.

    Grouped on the remedy, the same key the review note groups on, so the button
    sits beside the sentence describing what it will do.

    Two rules can also arrive at the *same* correction. A blue used for a shape
    fill and for a chart series is one colour in one package, reported by BR-004
    and by BR-011 and repaired by one rewrite. Planned twice, the second
    application finds the colour already gone and reports that it changed
    nothing -- a failure notice for work that succeeded, which is worse than
    either doing it twice or not offering it. Identical operations are therefore
    collapsed onto the fix that claimed them first, carrying every slide with
    them.
    """
    fixes: dict[str, Fix] = {}
    #: (kind, payload) -> the key of the fix already performing that operation.
    operations: dict[tuple[str, tuple[tuple[str, Any], ...]], str] = {}
    for finding in result.findings:
        builder = _BUILDERS.get(finding.rule_id)
        if builder is None:
            continue
        built = builder(finding, profile)
        if built is None:
            continue
        kind, payload, summary = built
        key = _action_key(finding)
        operation = (kind, _operation_signature(payload))
        key = operations.setdefault(operation, key)
        existing = fixes.get(key)
        slides = (finding.slide_index,)
        if existing is None:
            fixes[key] = Fix(
                key=key,
                rule_id=finding.rule_id,
                summary=summary,
                slides=slides,
                kind=kind,
                payload=payload,
            )
        elif finding.slide_index not in existing.slides:
            fixes[key] = Fix(
                key=existing.key,
                rule_id=existing.rule_id,
                summary=existing.summary,
                slides=tuple(sorted({*existing.slides, *slides})),
                kind=existing.kind,
                payload=existing.payload,
            )
    return fixes


def _operation_signature(payload: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    """A hashable reading of a payload, for recognising the same edit twice.

    Values that cannot be hashed are rendered, because the signature only has to
    distinguish operations rather than reconstruct them.
    """
    return tuple(
        (name, value if isinstance(value, str | int | float | bool | None) else repr(value))
        for name, value in sorted(payload.items())
    )


def action_key(
    rule_id: str, remedy: str | None, expected: str | None, message: str
) -> str:
    """The identity of one decision, shared with the review note.

    The note groups findings by the fix behind them; a correction is offered per
    group. Both compute the identity from the same three fields here, so neither
    can drift into naming the same decision differently.
    """
    return f"{rule_id}|{remedy or expected or message}"


def _action_key(finding: Finding) -> str:
    return action_key(
        finding.rule_id, finding.remedy, finding.expected, finding.message
    )


# --------------------------------------------------------------------------------------
# What a correction changed
# --------------------------------------------------------------------------------------


def finding_key(finding: Finding) -> str:
    """One finding's identity, stable across a re-audit.

    :func:`action_key` and the place it was found. The action key is already the
    tool's notion of one decision -- the review note groups on it and a
    correction is offered per group -- and it is built from the remedy, which
    every rule states in terms of the *expectation* rather than the measurement.
    "Move the logo to left 852pt, top 24pt" is the same sentence before and after
    the logo moves, so the finding keeps its identity while its measurement
    changes. A key built from the measurement would call every partial
    improvement a finding fixed and a different finding arrived.

    The location is appended because the action key is deliberately coarser than
    a finding: one off-palette gold on four slides is one decision and four
    findings, and a delta counts findings.
    """
    return (
        f"{_action_key(finding)}@{finding.slide_index}"
        f"#{finding.shape_name or ''}"
    )


@dataclass(frozen=True, slots=True)
class Changed:
    """One finding that arrived or left between two audits."""

    key: str
    rule_id: str
    severity: str
    slide: int
    shape: str | None
    message: str


@dataclass(frozen=True, slots=True)
class Delta:
    """What changed between the audit on screen and the one after a correction.

    Three counts, kept distinct because they answer different questions. *Fixed*
    is what the correction achieved, *remaining* is what is left, and *new* is
    the one that matters most: applying a correction can legitimately expose a
    finding that was masked before -- moving a shape onto its grid line can put
    it over its neighbour, which is precisely how automatic snapping damaged a
    real deck. Reporting only a total would hide that behind a number that went
    down, and the person would never know there was something to undo.
    """

    fixed: int
    remaining: int
    new: int
    before: int
    after: int
    #: The new findings in full, so the page can name them rather than count them.
    arrived: tuple[Changed, ...] = ()
    #: What left, for the record.
    cleared: tuple[Changed, ...] = ()

    @property
    def sentence(self) -> str:
        """The three counts as a person would say them."""
        parts = [f"{self.fixed} fixed", f"{self.remaining} remain"]
        if self.new:
            parts.append(f"{self.new} new")
        return ", ".join(parts)

    @property
    def worst_new(self) -> str | None:
        """The severity of the most serious finding the correction exposed."""
        if not self.arrived:
            return None
        return min(self.arrived, key=lambda c: SEVERITY_ORDER.get(c.severity, 9)).severity


def _changed(finding: Finding) -> Changed:
    return Changed(
        key=finding_key(finding),
        rule_id=finding.rule_id,
        severity=finding.severity,
        slide=finding.slide_index,
        shape=finding.shape_name,
        message=finding.message,
    )


def delta(before: Sequence[Finding] | None, after: Sequence[Finding]) -> Delta:
    """Diff two audits by finding identity.

    Takes findings rather than whole results, because the two sides have to be
    like for like and a caller sometimes holds only part of a result. The
    motivating case is content review: a semantic pass runs on an explicit check
    and not on a re-audit after a correction, so diffing whole results would
    report every semantic finding as fixed by a recolour that never touched
    them. The UI therefore keeps the deterministic findings and compares those.

    Counted with a :class:`~collections.Counter` rather than a set, so two
    findings that genuinely share an identity -- one rule reporting the same
    remedy twice about one shape -- are two findings in the arithmetic rather
    than one. That keeps the three counts reconciling: ``fixed + remaining`` is
    always the total before, and ``remaining + new`` always the total after. A
    delta whose numbers do not add up is worse than no delta.

    ``before`` is None for the first check of a deck, where nothing has changed
    yet and every finding is simply present.
    """
    after_findings = {finding_key(f): f for f in after}
    counted_after = Counter(finding_key(f) for f in after)
    if before is None:
        return Delta(
            fixed=0,
            remaining=len(after),
            new=0,
            before=len(after),
            after=len(after),
        )

    before_findings = {finding_key(f): f for f in before}
    counted_before = Counter(finding_key(f) for f in before)

    gone = counted_before - counted_after
    arrived = counted_after - counted_before
    return Delta(
        fixed=sum(gone.values()),
        remaining=sum((counted_before & counted_after).values()),
        new=sum(arrived.values()),
        before=len(before),
        after=len(after),
        arrived=tuple(
            _changed(after_findings[key])
            for key in sorted(
                arrived,
                key=lambda k: SEVERITY_ORDER.get(after_findings[k].severity, 9),
            )
        ),
        cleared=tuple(
            _changed(before_findings[key])
            for key in sorted(
                gone,
                key=lambda k: SEVERITY_ORDER.get(before_findings[k].severity, 9),
            )
        ),
    )


#: ``#RRGGBB`` out of a measurement like "#B08D3F, Delta-E 55.7 from #6B7280".
_HEX: Final[re.Pattern[str]] = re.compile(r"#([0-9A-Fa-f]{6})")


def _colour(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str] | None:
    """BR-004 and BR-011: the offending colour, and the palette colour to use.

    Read from the measurement rather than re-derived. The rule already resolved
    the nearest palette entry; doing it again here would be a second
    implementation of the same arithmetic, free to disagree with the first.
    """
    found = _HEX.findall(finding.measured or "")
    if len(found) != 2:
        return None
    wrong, right = found[0].upper(), found[1].upper()
    if wrong == right:
        return None
    return (
        "colour",
        {"from": wrong, "to": right},
        f"Recolour #{wrong} to #{right}",
    )


def _typeface(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str] | None:
    """BR-005: only where the house style approves exactly one typeface.

    With three approved faces there is no way to know which one a given run
    should have been, and picking the first alphabetically would be a guess
    dressed as a correction.
    """
    approved = sorted(profile.brand.fonts.allowed)
    wrong = (finding.measured or "").strip()
    if len(approved) != 1 or not wrong or wrong == approved[0]:
        return None
    return (
        "typeface",
        {"from": wrong, "to": approved[0]},
        f"Set {wrong} to {approved[0]}",
    )


def _notes(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str]:
    return "notes", {}, "Delete the speaker notes on every slide"


def _metadata(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str]:
    return "metadata", {}, "Clear the identifying document properties"


def _comments(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str]:
    return "comments", {}, "Delete every comment in the package"


def _whitespace(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str]:
    return (
        "whitespace",
        {},
        "Collapse double spaces, drop trailing spaces and spaces before punctuation",
    )


def _canon(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str] | None:
    """TY-005: the variant this finding names, and the canon term for it."""
    variant = (finding.measured or "").strip()
    canonical = (finding.expected or "").strip()
    if not variant or not canonical or variant == canonical:
        return None
    return (
        "term",
        {"from": variant, "to": canonical},
        f"Write {variant!r} as {canonical!r}",
    )


def _quotes(finding: Finding, profile: Profile) -> tuple[str, dict[str, Any], str] | None:
    convention = profile.typography.quotes
    if convention not in ("curly", "straight"):
        return None
    return "quotes", {"style": convention}, f"Convert quotes and apostrophes to {convention}"


#: rule id -> the builder that turns one of its findings into a fix, or None
#: where that particular finding cannot be corrected exactly.
_BUILDERS: Final[dict[str, Any]] = {
    "BR-004": _colour,
    "BR-011": _colour,
    "BR-005": _typeface,
    "HY-002": _notes,
    "HY-004": _metadata,
    "HY-005": _comments,
    "TY-001": _quotes,
    "TY-002": _whitespace,
    "TY-005": _canon,
}


# --------------------------------------------------------------------------------------
# Moving a shape: the one fix whose value comes from the person, not the tool
# --------------------------------------------------------------------------------------

#: The ``kind`` of a move, and the ``rule_id`` it carries. Not a real rule id --
#: no rule produces a move -- but ``Fix.rule_id`` is how a caller tells one fix
#: from another, and a move that claimed to be LO-003's correction would be
#: saying TieOut chose the position. It did not.
MOVE_KIND: Final[str] = "move"
MOVE_RULE_ID: Final[str] = "MOVE"


def move_key(slide_index: int, shape_id: int) -> str:
    """The identity of "this shape, on this slide".

    Deliberately not an :func:`action_key`. An action key names a decision the
    audit found; this names a shape a person picked up. Keeping the two in
    separate namespaces is what stops a move ever being looked up as a planned
    correction, or a planned correction being applied as a move.
    """
    return f"{MOVE_RULE_ID}|{slide_index}|{shape_id}"


def move_fix(
    *,
    slide_index: int,
    shape_id: int,
    shape_name: str,
    x_emu: int,
    y_emu: int,
    cx_emu: int,
    cy_emu: int,
) -> Fix:
    """A fix that puts one shape at one position, in EMU, exactly as given.

    **Every number here came from the person.** ``x_emu`` and ``y_emu`` are where
    they dragged or nudged the shape to; nothing in this module rounds them,
    re-snaps them, or checks them against a grid line. That is the whole reason
    this is allowed to write geometry when nothing else in TieOut is: the tool is
    not deciding where the shape belongs, it is recording where someone put it.

    The units are EMU and they are integers, which is not incidental. A nudge is
    ``EMU_PER_POINT`` exactly, so ten nudges out and ten back is integer addition
    that lands on the offset it started from -- where points would accumulate
    floating-point error into a shape that never quite returns.

    ``cx_emu`` and ``cy_emu`` are the shape's own size, and are used only when
    the shape has no transform of its own -- an untouched placeholder inheriting
    its box from the layout. Writing an offset there means writing a whole
    ``a:xfrm``, and a transform with an offset and no extent is not valid OOXML.
    The extent written is the one the shape already resolves to, so the shape
    keeps the size it had; this is what PowerPoint itself does the first time
    someone drags a placeholder.
    """
    return Fix(
        key=move_key(slide_index, shape_id),
        rule_id=MOVE_RULE_ID,
        summary=(
            f"Move {shape_name or f'shape {shape_id}'} on slide {slide_index} to "
            f"{x_emu / EMU_PER_POINT:.1f}, {y_emu / EMU_PER_POINT:.1f}pt"
        ),
        slides=(slide_index,),
        kind=MOVE_KIND,
        payload={
            "slide": slide_index,
            "shape_id": shape_id,
            "x_emu": int(x_emu),
            "y_emu": int(y_emu),
            "cx_emu": int(cx_emu),
            "cy_emu": int(cy_emu),
        },
    )


# --------------------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------------------


def apply_fix(source: Path, target: Path, fix: Fix) -> FixReport:
    """Apply one fix, writing the corrected deck to ``target``.

    Never in place. The caller keeps the file it had, which is what makes undo a
    matter of putting a path back rather than of inverting an edit -- and an edit
    that cannot be inverted exactly is an edit that cannot be undone honestly.
    """
    handler = _APPLIERS.get(fix.kind)
    if handler is None:  # pragma: no cover - a fix is only planned with an applier
        return FixReport(key=fix.key, applied=False, detail=f"no applier for {fix.kind}")

    shutil.copyfile(source, target)
    try:
        changes = handler(target, fix)
    except MoveFailed as exc:
        # Already a sentence written for the person holding the mouse, so it is
        # passed through rather than wrapped in a type name.
        shutil.copyfile(source, target)
        return FixReport(key=fix.key, applied=False, detail=str(exc))
    except Exception as exc:
        # A fix that fails must leave the deck as it was, not half-edited.
        shutil.copyfile(source, target)
        return FixReport(
            key=fix.key, applied=False, detail=f"{type(exc).__name__}: {exc}"
        )

    if not changes:
        shutil.copyfile(source, target)
        return FixReport(
            key=fix.key,
            applied=False,
            detail="nothing in the package matched, so nothing was changed",
        )
    return FixReport(key=fix.key, applied=True, changes=changes)


def _rewrite_parts(
    path: Path, prefixes: tuple[str, ...], rewrite: Any
) -> int:
    """Run ``rewrite`` over every part whose name starts with one of ``prefixes``.

    Rewriting the zip wholesale rather than editing in place: a .pptx is a zip,
    and zips do not support replacing an entry without rebuilding them.
    """
    changes = 0
    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}

    for name in list(items):
        if not name.startswith(prefixes) or not name.endswith(".xml"):
            continue
        before = items[name].decode("utf-8")
        after, count = rewrite(before)
        if count:
            items[name] = after.encode("utf-8")
            changes += count

    if changes:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
            for name, payload in items.items():
                out.writestr(name, payload)
    return changes


def _apply_colour(path: Path, fix: Fix) -> int:
    """Substitute one sRGB value for another wherever it is stated explicitly.

    At the XML level because a colour reaches a slide by many routes -- a shape
    fill, an outline, a run, a table cell, a chart series -- and one substitution
    over ``a:srgbClr`` catches all of them. Only an explicit value is touched: a
    ``schemeClr`` is the theme's to decide, and rewriting it here would change
    colours the finding never mentioned.
    """
    wrong, right = fix.payload["from"], fix.payload["to"]
    pattern = re.compile(rf'(srgbClr\s+val=")({wrong})(")', re.IGNORECASE)

    def rewrite(xml: str) -> tuple[str, int]:
        return pattern.subn(rf"\g<1>{right}\g<3>", xml)

    return _rewrite_parts(path, _CONTENT_PARTS, rewrite)


def _apply_typeface(path: Path, fix: Fix) -> int:
    wrong, right = fix.payload["from"], fix.payload["to"]
    pattern = re.compile(rf'(typeface=")({re.escape(wrong)})(")')

    def rewrite(xml: str) -> tuple[str, int]:
        return pattern.subn(rf"\g<1>{right}\g<3>", xml)

    return _rewrite_parts(path, _CONTENT_PARTS, rewrite)


def _apply_metadata(path: Path, fix: Fix) -> int:
    """Empty the docProps fields HY-004 reports, and only those.

    The title and the created date are not identifying and are left alone: a
    deck with no title is not more private, only harder to find.
    """
    changes = 0
    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}

    for part, tags in (
        ("docProps/core.xml", ("dc:creator", "cp:lastModifiedBy", "cp:category",
                               "cp:keywords")),
        ("docProps/app.xml", ("Company", "Manager")),
    ):
        payload = items.get(part)
        if payload is None:
            continue
        xml = payload.decode("utf-8")
        for tag in tags:
            xml, count = re.subn(
                rf"<{tag}>(?!</{tag}>).*?</{tag}>", f"<{tag}></{tag}>", xml, flags=re.S
            )
            changes += count
        items[part] = xml.encode("utf-8")

    if changes:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
            for name, part_payload in items.items():
                out.writestr(name, part_payload)
    return changes


def _apply_notes(path: Path, fix: Fix) -> int:
    """Empty every notes text frame, leaving the notes slides themselves.

    Deleting the parts would mean rewriting the relationships that point at
    them; emptying the text removes everything a reader could see, which is what
    the finding is about.
    """
    body = re.compile(r"(<p:txBody>).*?(</p:txBody>)", re.S)

    def rewrite(xml: str) -> tuple[str, int]:
        # The slide-image placeholder on a notes slide has no text body, so only
        # the notes placeholder is affected.
        return body.subn(
            r"\1<a:bodyPr/><a:lstStyle/><a:p/>\2", xml
        )

    return _rewrite_parts(path, ("ppt/notesSlides/notesSlide",), rewrite)


def _apply_comments(path: Path, fix: Fix) -> int:
    """Remove every comment, its relationship and its content-type override."""
    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}

    doomed = [
        name
        for name in items
        if name.startswith(("ppt/comments/", "ppt/modernComments/"))
        or name.startswith("ppt/commentAuthors")
    ]
    if not doomed:
        return 0
    for name in doomed:
        del items[name]

    targets = {Path(name).name for name in doomed}
    relationship = re.compile(
        r"<Relationship\b[^>]*Target=\"[^\"]*(" + "|".join(re.escape(t) for t in targets)
        + r")\"[^>]*/>"
    )
    override = re.compile(
        r"<Override\b[^>]*PartName=\"/(" + "|".join(re.escape(n) for n in doomed)
        + r")\"[^>]*/>"
    )
    for name in list(items):
        if name.endswith(".rels"):
            xml, count = relationship.subn("", items[name].decode("utf-8"))
            if count:
                items[name] = xml.encode("utf-8")
        elif name == "[Content_Types].xml":
            xml, _ = override.subn("", items[name].decode("utf-8"))
            items[name] = xml.encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, payload in items.items():
            out.writestr(name, payload)
    return len(doomed)


#: Whitespace corrections, applied to the text of a run and nowhere else.
_TEXT_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # A padded field separator is deliberate -- see TY-002's own exemption -- so
    # the double-space rule leaves a run of spaces against one alone.
    (re.compile(r"(?<![|│•·—–]) {2,}(?![|│•·—–])"), " "),
    (re.compile(r"[ \t]+([,.;:!?])"), r"\1"),
)

#: Applied only to the last run of a paragraph -- see :func:`_apply_text`.
_TRAILING: Final[re.Pattern[str]] = re.compile(r"[ \t]+$")


def _apply_text(path: Path, fix: Fix) -> int:
    """Edit the text of ``a:t`` elements, and only their text.

    Paragraph by paragraph, because one of the corrections depends on where a
    run sits. A run ending in a space is not trailing whitespace: it is the
    space between two runs of the same sentence, and stripping it deletes the
    gap between the words. On a real deck that turned "Prepared for:  The
    Board" into "Prepared for:The Board" -- a fix that made the slide worse than
    the defect it corrected. Only the last run of a paragraph can carry trailing
    whitespace, so only the last run is asked.

    Scoped to each element's content so that no substitution can reach an
    attribute, a tag or the run properties beside it.
    """
    kind = fix.kind
    payload = fix.payload
    paragraph = re.compile(r"<a:p>.*?</a:p>", re.S)
    element = re.compile(r"(<a:t>)(.*?)(</a:t>)", re.S)

    def convert(text: str, *, last: bool) -> str:
        out = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        if kind == "whitespace":
            for pattern, replacement in _TEXT_RULES:
                out = pattern.sub(replacement, out)
            if last:
                out = _TRAILING.sub("", out)
        elif kind == "term":
            out = re.sub(rf"\b{re.escape(payload['from'])}\b", payload["to"], out)
        elif kind == "quotes":
            out = _requote(out, payload["style"])
        return out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def rewrite(xml: str) -> tuple[str, int]:
        changed = 0

        def one_paragraph(block: re.Match[str]) -> str:
            nonlocal changed
            body = block.group(0)
            runs = list(element.finditer(body))
            if not runs:
                return body
            out: list[str] = []
            cursor = 0
            for index, run in enumerate(runs):
                out.append(body[cursor : run.start()])
                before = run.group(2)
                after = convert(before, last=index == len(runs) - 1)
                if after != before:
                    changed += 1
                out.append(f"{run.group(1)}{after}{run.group(3)}")
                cursor = run.end()
            out.append(body[cursor:])
            return "".join(out)

        return paragraph.sub(one_paragraph, xml), changed

    return _rewrite_parts(path, _CONTENT_PARTS, rewrite)


def _requote(text: str, style: str) -> str:
    if style == "straight":
        return (
            text.replace("‘", "'").replace("’", "'")
            .replace("“", '"').replace("”", '"')
        )
    # Curly: an apostrophe inside a word is always a right single quote, and the
    # paired forms alternate. Anything else would need to know about nesting.
    out = re.sub(r"(?<=\w)'(?=\w)", "’", text)
    out = re.sub(r'"([^"]*)"', "“\\1”", out)
    return re.sub(r"'([^']*)'", "‘\\1’", out)


# --------------------------------------------------------------------------------------
# Writing a transform
# --------------------------------------------------------------------------------------

_NS: Final[dict[str, str]] = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

_A: Final[str] = _NS["a"]
_R_ID: Final[str] = f"{{{_NS['r']}}}id"

#: Where each kind of shape keeps its transform, in the order the loader looks.
#: A ``graphicFrame`` -- every table and every chart -- uses ``p:xfrm`` in the
#: presentation namespace rather than ``a:xfrm``, and a group keeps its own in
#: ``p:grpSpPr``. Reading only ``p:spPr/a:xfrm`` would silently move nothing on a
#: chart, which is exactly the shape this feature exists to move.
_XFRM_PATHS: Final[tuple[str, ...]] = ("p:spPr/a:xfrm", "p:xfrm", "p:grpSpPr/a:xfrm")

#: Where to put an ``a:xfrm`` that has to be created, per shape element. The
#: schema fixes the order of a ``spPr``'s children and ``a:xfrm`` is first, so a
#: transform appended to the end produces a file PowerPoint refuses to open.
_XFRM_PARENT: Final[dict[str, str]] = {
    "sp": "p:spPr",
    "pic": "p:spPr",
    "cxnSp": "p:spPr",
    "grpSp": "p:grpSpPr",
}


def _slide_parts(items: dict[str, bytes]) -> list[str]:
    """The slide part names, in the order the deck presents them.

    Resolved from ``p:sldIdLst`` and the presentation's relationships rather than
    by sorting ``slide1.xml``, ``slide2.xml`` ... Those names are allocated in
    creation order, not presentation order, so a deck whose slides have ever been
    reordered numbers them differently from how it reads -- and a move applied to
    the wrong slide is the worst failure this feature has, because it is silent.
    """
    presentation = items.get("ppt/presentation.xml")
    rels = items.get("ppt/_rels/presentation.xml.rels")
    if presentation is None or rels is None:
        raise MoveFailed("this package has no presentation part to read slide order from")

    targets: dict[str, str] = {}
    for relationship in etree.fromstring(rels):
        identifier = relationship.get("Id")
        target = relationship.get("Target")
        if not identifier or not target:
            continue
        # A target is relative to the part holding the relationship, so a slide
        # reads as "slides/slide1.xml" from ppt/presentation.xml. An absolute
        # one is relative to the package root instead.
        resolved = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.join("ppt", target)
        )
        targets[identifier] = posixpath.normpath(resolved)

    ordered: list[str] = []
    for entry in etree.fromstring(presentation).findall("p:sldIdLst/p:sldId", _NS):
        target = targets.get(entry.get(_R_ID) or "")
        if target is not None:
            ordered.append(target)
    return ordered


def _shape_element(tree: etree._Element, shape_id: int) -> etree._Element:
    """The top-level shape on this slide with this id.

    ``./*/p:cNvPr`` rather than ``.//p:cNvPr`` on purpose: it reaches the shape's
    own non-visual properties and stops there. A descendant search would match a
    shape *inside* a group and then write an offset in the group's child
    coordinate space as though it were slide space, which moves the shape
    somewhere nobody asked for.
    """
    for child in tree:
        properties = child.find("./*/p:cNvPr", _NS)
        if properties is None:
            continue
        try:
            found = int(properties.get("id") or "")
        except ValueError:
            continue
        if found == shape_id:
            return child
    raise MoveFailed(
        f"no shape with id {shape_id} at the top level of that slide. "
        "Re-run the check and try the move again."
    )


def _transform(element: etree._Element, cx_emu: int, cy_emu: int) -> etree._Element:
    """This shape's ``a:xfrm``, created with the size it already has if absent."""
    for path in _XFRM_PATHS:
        existing = element.find(path, _NS)
        if existing is not None:
            return existing

    tag = etree.QName(element).localname
    parent_path = _XFRM_PARENT.get(tag)
    if parent_path is None:
        # A graphicFrame's p:xfrm is required by the schema, so reaching here
        # means the part is malformed rather than merely sparse.
        raise MoveFailed(f"a {tag} with no transform cannot be given one safely")
    parent = element.find(parent_path, _NS)
    if parent is None:
        raise MoveFailed(f"that shape has no {parent_path} to hold a transform")

    xfrm = etree.Element(f"{{{_A}}}xfrm")
    parent.insert(0, xfrm)
    extent = etree.SubElement(xfrm, f"{{{_A}}}ext")
    extent.set("cx", str(cx_emu))
    extent.set("cy", str(cy_emu))
    return xfrm


def _apply_move(path: Path, fix: Fix) -> int:
    """Write one shape's offset, and touch nothing else in the package.

    Only ``a:off``'s ``x`` and ``y`` are assigned. The extent, the rotation, the
    flips and every other attribute on the transform are left exactly as they
    were, because the person moved the shape -- they did not resize it, turn it
    or mirror it, and a fix that changed more than it was asked to is the defect
    this module exists to avoid.
    """
    payload = fix.payload
    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}

    parts = _slide_parts(items)
    index = int(payload["slide"])
    if not 1 <= index <= len(parts):
        raise MoveFailed(f"this deck has no slide {index}")
    part = parts[index - 1]
    if part not in items:
        raise MoveFailed(f"slide {index} points at {part}, which is not in the package")

    root = etree.fromstring(items[part])
    tree = root.find("p:cSld/p:spTree", _NS)
    if tree is None:
        raise MoveFailed(f"slide {index} has no shape tree")

    element = _shape_element(tree, int(payload["shape_id"]))
    xfrm = _transform(element, int(payload["cx_emu"]), int(payload["cy_emu"]))

    offset = xfrm.find("a:off", _NS)
    if offset is None:
        offset = etree.Element(f"{{{_A}}}off")
        xfrm.insert(0, offset)  # a:off precedes a:ext; the schema fixes the order
    offset.set("x", str(int(payload["x_emu"])))
    offset.set("y", str(int(payload["y_emu"])))

    items[part] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, content in items.items():
            out.writestr(name, content)
    return 1


_APPLIERS: Final[dict[str, Any]] = {
    MOVE_KIND: _apply_move,
    "colour": _apply_colour,
    "typeface": _apply_typeface,
    "metadata": _apply_metadata,
    "notes": _apply_notes,
    "comments": _apply_comments,
    "whitespace": _apply_text,
    "term": _apply_text,
    "quotes": _apply_text,
}
