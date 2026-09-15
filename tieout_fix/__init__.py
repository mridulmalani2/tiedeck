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

Geometry is never fixed, and that is a finding rather than a policy. Snapping the
six shapes LO-003 reported on a real deck to their nearest grid line was tried:
it drove one text box into its neighbour -- a *major* overlap where there had
been none -- and turned a timetable column spaced evenly to the point into one
varying by five. The grid lines were derived from that deck, different shapes
align to different ones, and pulling a group onto a line breaks its relationship
with everything around it. Which alignment matters is a judgement about what the
slide is for. So LO-*, the logo rules and BR-008 report and stop.

Nor is anything fixed where the tool can see a problem but not the answer. Two
figures that disagree, a total that does not sum, a placeholder that needs real
words, a word the dictionary does not know, text that overflows its box: the
tool knows something is wrong and has no way to know what is right. Inventing a
value there would be worse than silence, because it would be wrong invisibly.

Everything this package declines to fix is still reported. The deck a person
gets back is the deck they gave, with the mechanical corrections made and every
judgement still theirs.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from tieout.profile.schema import Profile
from tieout.rules.base import AuditResult, Finding

__all__ = ["Fix", "FixReport", "action_key", "apply_fix", "plan_fixes"]

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


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------


def plan_fixes(result: AuditResult, profile: Profile) -> dict[str, Fix]:
    """Every finding that can be corrected exactly, keyed by its action.

    Grouped on the remedy, the same key the review note groups on, so the button
    sits beside the sentence describing what it will do.
    """
    fixes: dict[str, Fix] = {}
    for finding in result.findings:
        builder = _BUILDERS.get(finding.rule_id)
        if builder is None:
            continue
        built = builder(finding, profile)
        if built is None:
            continue
        kind, payload, summary = built
        key = _action_key(finding)
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


_APPLIERS: Final[dict[str, Any]] = {
    "colour": _apply_colour,
    "typeface": _apply_typeface,
    "metadata": _apply_metadata,
    "notes": _apply_notes,
    "comments": _apply_comments,
    "whitespace": _apply_text,
    "term": _apply_text,
    "quotes": _apply_text,
}
