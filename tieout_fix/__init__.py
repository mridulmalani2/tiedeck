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
figures that each *state* something and disagree, a placeholder that needs real
words, a word the dictionary does not know, text that overflows its box: the
tool knows something is wrong and has no way to know what is right. Inventing a
value there would be worse than silence, because it would be wrong invisibly.

**A derived figure is the exception, and it is not an inventing one.** A margin
is EBITDA over revenue, a multiple is EV over EBITDA, a column total is the sum
of its column, a bridge's close is its opening plus its steps. Where the deck
prints the inputs, the answer is arithmetic on the deck's own numbers, and the
convention -- in this tool and in the modelling it audits -- is that the derived
figure yields to its inputs, because the inputs are the primary facts and the
derived figure is computed from them. So CO-003 to CO-007 get a correction and
CO-001 and CO-002 do not: there neither figure is derived from the other, and
which is right is a judgement about the deal.

CO-003 sat on the wrong side of that line until the derived checks landed, on
the argument that a total which does not sum might be a wrong total or a wrong
row. It might; the convention settles it, the finding states both numbers, and
undo is one click. The distinction that matters is not "could the other side be
wrong" -- it always could -- but whether the deck itself determines an answer.

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
    "RECELL_KIND",
    "RESIZE_KIND",
    "RESIZE_RULE_ID",
    "RETEXT_KIND",
    "RETEXT_RULE_ID",
    "Changed",
    "Delta",
    "Fix",
    "FixReport",
    "MoveFailed",
    "action_key",
    "apply_fix",
    "delta",
    "finding_key",
    "group_geometry_refusal",
    "move_fix",
    "move_key",
    "plan_fixes",
    "recell_fix",
    "recell_key",
    "resize_fix",
    "resize_key",
    "retext_fix",
    "retext_key",
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


def _derived_figure(
    finding: Finding, profile: Profile
) -> tuple[str, dict[str, Any], str] | None:
    """A tie-out correction, where the rule computed one and it can be written.

    The whole of the decision is already on the finding: PLAN.md §5.5 draws the
    line between a *derived* figure, which has one right answer, and two
    *stated* figures that disagree, where which is right is a judgement about
    the deal. :class:`tieout.rules.base.Correction` carries which of the two
    this is, so nothing here re-derives it -- a second implementation of every
    rule's arithmetic is exactly how a tool ends up writing a number no test
    covered.

    Four things stop a fix being offered, and each is a deliberate refusal:
    a correction the rule did not attach; an ``edit`` rather than a ``fix``;
    a figure the write path cannot reach (``refused``, which the page shows as
    a sentence rather than a dead button); and a replacement identical to what
    is already there.
    """
    correction = finding.correction
    if correction is None or correction.kind != "fix":
        return None
    if not correction.writable or not correction.replacement:
        return None
    if correction.replacement == correction.current:
        return None  # pragma: no cover - a fix that changes nothing is not reported
    if correction.source != "table":
        # Prose figures reach `retext_fix`; nothing else is written at all.
        if correction.source != "text":
            return None
        return (
            RETEXT_KIND,
            {
                "slide": finding.slide_index,
                "shape_id": correction.shape_id,
                "paragraph": correction.address[0],
                "run": correction.address[1],
                "text": correction.replacement,
            },
            f"Set {correction.current} to {correction.replacement}",
        )
    row, column = correction.address
    return (
        RECELL_KIND,
        {
            "slide": finding.slide_index,
            "shape_id": correction.shape_id,
            "row": row,
            "column": column,
            "paragraph": correction.cell_paragraph,
            "run": correction.cell_run,
            "text": correction.replacement,
        },
        f"Set {correction.current} to {correction.replacement}",
    )


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
    # The derived tie-out rules. Each recomputes a figure the deck's own
    # numbers determine, so the correction is arithmetic; CO-001, CO-002,
    # CO-008 and CO-009 are deliberately absent, because there the deck states
    # two figures and choosing between them is not TieOut's to do.
    "CO-003": _derived_figure,
    "CO-004": _derived_figure,
    "CO-005": _derived_figure,
    "CO-006": _derived_figure,
    "CO-007": _derived_figure,
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
    current_x_emu: int,
    current_y_emu: int,
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

    ``current_x_emu`` and ``current_y_emu`` are the shape's own position
    *before* the move, in slide space -- the one thing this function does not
    otherwise know and cannot re-derive from ``x_emu``/``y_emu`` alone. They
    are used only when the shape sits inside a group: writing a slide-space
    number straight into a grouped shape's ``a:off`` would put it somewhere
    nobody asked for, so the write path needs the slide-space *move*
    (``x_emu - current_x_emu``) to convert into the group's own coordinate
    space. A shape with no group ignores both.
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
            "current_x_emu": int(current_x_emu),
            "current_y_emu": int(current_y_emu),
        },
    )


# --------------------------------------------------------------------------------------
# Resizing a shape: the same exception as a move, for the same reason
# --------------------------------------------------------------------------------------

#: Not a real rule id, for the reason :data:`MOVE_RULE_ID` is not one: no rule
#: proposes a size, so a resize that claimed one would be saying TieOut chose
#: it.
RESIZE_KIND: Final[str] = "resize"
RESIZE_RULE_ID: Final[str] = "RESIZE"


def resize_key(slide_index: int, shape_id: int) -> str:
    """The identity of "this shape, on this slide, being resized".

    A sibling of :func:`move_key` rather than the same string: a move and a
    resize are two different edits to one shape's transform, and giving them
    one identity would be indistinguishable to anything that ever keys a
    lock, a rejection or a log entry on it -- :data:`RESIZE_RULE_ID` keeps
    this in its own namespace for exactly the reason ``MOVE_RULE_ID`` keeps
    a move out of :func:`action_key`'s.
    """
    return f"{RESIZE_RULE_ID}|{slide_index}|{shape_id}"


def resize_fix(
    *,
    slide_index: int,
    shape_id: int,
    shape_name: str,
    x_emu: int,
    y_emu: int,
    cx_emu: int,
    cy_emu: int,
    current_cx_emu: int,
    current_cy_emu: int,
) -> Fix:
    """A fix that gives one shape one size, in EMU, exactly as given.

    The mirror of :func:`move_fix`, with the two halves of the transform
    swapped: ``cx_emu`` and ``cy_emu`` are the size a person dragged a handle
    to, written with no rounding and no judgement about whether it is a good
    size -- TieOut does not resize shapes on its own account any more than it
    moves them. ``x_emu`` and ``y_emu`` are the shape's own current position,
    carried along only for the case :func:`move_fix` carries its own size
    along for: a shape with no transform of its own yet, where a fresh
    ``a:xfrm`` must be written whole or not at all.

    ``current_cx_emu`` and ``current_cy_emu`` are the shape's own size
    *before* the resize, in slide space -- the mirror of ``move_fix``'s
    ``current_x_emu``/``current_y_emu``, needed for exactly the same reason:
    a shape inside a group writes into its own coordinate space, and the
    write path needs the slide-space *change in size* to convert into it.
    A shape with no group ignores both.
    """
    return Fix(
        key=resize_key(slide_index, shape_id),
        rule_id=RESIZE_RULE_ID,
        summary=(
            f"Resize {shape_name or f'shape {shape_id}'} on slide {slide_index} to "
            f"{cx_emu / EMU_PER_POINT:.1f}×{cy_emu / EMU_PER_POINT:.1f}pt"
        ),
        slides=(slide_index,),
        kind=RESIZE_KIND,
        payload={
            "slide": slide_index,
            "shape_id": shape_id,
            "x_emu": int(x_emu),
            "y_emu": int(y_emu),
            "cx_emu": int(cx_emu),
            "cy_emu": int(cy_emu),
            "current_cx_emu": int(current_cx_emu),
            "current_cy_emu": int(current_cy_emu),
        },
    )


# --------------------------------------------------------------------------------------
# Editing text: the third exception, addressed by position rather than pattern
# --------------------------------------------------------------------------------------

#: Not a real rule id, for the reason :data:`MOVE_RULE_ID` is not one: no rule
#: proposes replacement text, so an edit that claimed one would be saying
#: TieOut chose the words. A person did.
RETEXT_KIND: Final[str] = "retext"
RETEXT_RULE_ID: Final[str] = "RETEXT"


def retext_key(slide_index: int, shape_id: int, paragraph: int, run: int) -> str:
    """The identity of one run, on one shape, on one slide.

    Finer-grained than :func:`move_key`, because a move or a resize has only
    one transform to contend for but a shape can hold many runs, each an
    independent edit. Two edits to two different runs on the same shape are
    two corrections, not one applied twice.
    """
    return f"{RETEXT_RULE_ID}|{slide_index}|{shape_id}|{paragraph}|{run}"


#: Rewriting one figure inside a table cell.
#:
#: A separate kind from ``retext`` because the XML path is different and the
#: difference is not cosmetic: a table's text lives in
#: ``a:tbl/a:tr/a:tc/a:txBody``, reached through a ``graphicFrame``, and the
#: ``p:txBody`` :func:`_apply_retext` looks for is simply not there. Before this
#: existed, every correction the tie-out rules could compute landed on a cell,
#: and every one of them would have raised "that shape has no text frame" --
#: which is why PLAN.md §6 listed the table-cell write path as the thing that
#: "must refuse, visibly, rather than appearing to work". It no longer has to
#: refuse.
RECELL_KIND: Final[str] = "recell"


def recell_key(
    slide_index: int, shape_id: int, row: int, column: int, paragraph: int, run: int
) -> str:
    """The identity of one figure, in one cell, of one table, on one slide.

    As fine-grained as :func:`retext_key` and for the same reason: a table
    holds many figures and two corrections to two cells are two decisions.
    """
    return f"RECELL|{slide_index}|{shape_id}|{row}|{column}|{paragraph}|{run}"


def recell_fix(
    *,
    slide_index: int,
    shape_id: int,
    shape_name: str,
    row: int,
    column: int,
    paragraph: int,
    run: int,
    text: str,
) -> Fix:
    """A fix that sets one run of one table cell to ``text``.

    Positional throughout, like :func:`retext_fix`: row, column, paragraph and
    run, because the same digits legitimately appear in other cells that were
    never wrong. Nothing but the run's ``<a:t>`` is touched, so the cell keeps
    its font, its fill, its alignment and any footnote marker sitting in the
    run beside it.
    """
    return Fix(
        key=recell_key(slide_index, shape_id, row, column, paragraph, run),
        rule_id=RETEXT_RULE_ID,
        summary=(
            f"Set row {row + 1}, column {column + 1} of "
            f"{shape_name or f'shape {shape_id}'} on slide {slide_index} to {text}"
        ),
        slides=(slide_index,),
        kind=RECELL_KIND,
        payload={
            "slide": slide_index,
            "shape_id": shape_id,
            "row": int(row),
            "column": int(column),
            "paragraph": int(paragraph),
            "run": int(run),
            "text": text,
        },
    )


def retext_fix(
    *,
    slide_index: int,
    shape_id: int,
    shape_name: str,
    paragraph: int,
    run: int,
    text: str,
) -> Fix:
    """A fix that sets one run's text to exactly what a person typed.

    **Positional, not a substring match.** ``_apply_text`` above finds every
    occurrence of a variant spelling or a double space because a rule found
    that *pattern* wrong wherever it appears; this finds the third run of the
    second paragraph of one specific shape, because a person editing text on
    the canvas is correcting the one occurrence in front of them, and the same
    characters can legitimately appear elsewhere in the deck as something that
    was never wrong. Addressing by content instead would edit every match, and
    the first time two slides happened to share a sentence it would edit the
    slide nobody was looking at as well as the one that was.

    Nothing about the run except its ``<a:t>`` content is touched -- its
    resolved font, its language and its position in the paragraph survive
    exactly as they were, because those are facts about the shape's design
    that an edit to its words has no license to change.
    """
    return Fix(
        key=retext_key(slide_index, shape_id, paragraph, run),
        rule_id=RETEXT_RULE_ID,
        summary=f"Edit text on {shape_name or f'shape {shape_id}'} on slide {slide_index}",
        slides=(slide_index,),
        kind=RETEXT_KIND,
        payload={
            "slide": slide_index,
            "shape_id": shape_id,
            "paragraph": int(paragraph),
            "run": int(run),
            "text": text,
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
_P: Final[str] = _NS["p"]
_R_ID: Final[str] = f"{{{_NS['r']}}}id"
_XML_SPACE: Final[str] = "{http://www.w3.org/XML/1998/namespace}space"

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


#: Elements a move, a resize or a text edit can ever target. Not ``grpSp``:
#: nothing in this module writes a group's own transform, only what sits
#: inside one.
_GEOMETRY_TAGS: Final[tuple[str, ...]] = ("sp", "pic", "graphicFrame", "cxnSp")


def _shape_element_anywhere(tree: etree._Element, shape_id: int) -> etree._Element:
    """This shape wherever it sits, including inside a group.

    A descendant search, deliberately: earlier this stopped at the top level,
    because a grouped shape's geometry is stored in the group's own
    coordinate space and writing a slide-space number there would move the
    shape somewhere nobody asked for. That hazard is now handled explicitly,
    by :func:`_group_target` converting into the group's space before
    anything is written, rather than by refusing to find the shape at all --
    which is also what makes editing a run's text safe to reach into a group
    for: no coordinate space stands between an edit to a shape's words and
    the group it happens to sit in.
    """
    tags = tuple(f"{{{_P}}}{name}" for name in _GEOMETRY_TAGS)
    for element in tree.iter(*tags):
        properties = element.find("./*/p:cNvPr", _NS)
        if properties is None:
            continue
        try:
            found = int(properties.get("id") or "")
        except ValueError:
            continue
        if found == shape_id:
            return element
    raise MoveFailed(
        f"no shape with id {shape_id} on that slide. "
        "Re-run the check and try again."
    )


def _ancestor_groups(element: etree._Element) -> list[etree._Element]:
    """This shape's ancestor ``p:grpSp`` elements, innermost first.

    Stops at the slide's own shape tree, so a shape is never walked past the
    slide it is on. Shared by the write path, which needs each ancestor's
    scale to convert a slide-space delta into this shape's own coordinate
    space, and by :func:`group_geometry_refusal`, which needs to know the
    same ancestors to say up front whether that conversion is even possible.
    """
    groups: list[etree._Element] = []
    node = element.getparent()
    while node is not None:
        local = etree.QName(node).localname
        if local == "spTree":
            break
        if local == "grpSp":
            groups.append(node)
        node = node.getparent()
    return groups


def _group_scale(group: etree._Element) -> tuple[float, float]:
    """``(scale_x, scale_y)`` one group applies to its children's coordinates.

    Only the scale, never the offset. A slide-space *difference* -- the
    amount a person moved or resized a shape by -- survives translation
    unchanged; only its *scale* has to be un-done to land in the group's own
    units, so that is the only piece of the group's transform this needs to
    read. Raises where the group also rotates or mirrors its children, which
    turns "un-scale it" into a real rotation this module does not do.
    """
    xfrm = group.find("p:grpSpPr/a:xfrm", _NS)
    if xfrm is None:
        return (1.0, 1.0)
    rotation = int(xfrm.get("rot") or 0)
    if rotation or xfrm.get("flipH") == "1" or xfrm.get("flipV") == "1":
        raise MoveFailed(
            "this shape is inside a group that is itself rotated or "
            "mirrored, which cannot be written into safely here. Ungroup it "
            "in PowerPoint to move or resize it."
        )
    ext = xfrm.find("a:ext", _NS)
    ch_ext = xfrm.find("a:chExt", _NS)
    ext_cx = int((ext.get("cx") if ext is not None else None) or 0)
    ext_cy = int((ext.get("cy") if ext is not None else None) or 0)
    ch_ext_cx = int((ch_ext.get("cx") if ch_ext is not None else None) or 0)
    ch_ext_cy = int((ch_ext.get("cy") if ch_ext is not None else None) or 0)
    if not ch_ext_cx or not ch_ext_cy:
        raise MoveFailed("that group's coordinate space is degenerate")
    return (ext_cx / ch_ext_cx, ext_cy / ch_ext_cy)


def group_geometry_refusal(element: Any) -> str:
    """Whether a move or a resize on this shape can be written safely, given
    the groups it sits inside -- empty when it can.

    Read-only and cheap enough to call from :mod:`tieout_ui.view`, which asks
    it of every grouped shape an audit reports on. It answers the same
    question :func:`_group_target` answers by actually doing the conversion,
    checked here without writing anything, so the page can say up front which
    shapes it can move and word the refusal the same way the write path would
    fail with.
    """
    try:
        groups = _ancestor_groups(element)
        for group in groups:
            _group_scale(group)
    except MoveFailed as exc:
        return str(exc)
    return ""


def _group_target(
    element: etree._Element,
    *,
    slide_dx_emu: int,
    slide_dy_emu: int,
    slide_dcx_emu: int,
    slide_dcy_emu: int,
) -> tuple[int, int, int, int] | None:
    """Where a slide-space *change* lands in this shape's own coordinates, or
    ``None`` when the shape is not inside a group and slide space already is
    its own.

    Takes a change rather than an absolute target on purpose. The shape's
    current position and size in slide space are not re-derived here -- doing
    that would mean reimplementing the loader's own offset composition a
    second time, free to disagree with it -- so instead this un-scales the
    *difference* a move or a resize represents, which needs only the product
    of each ancestor group's scale and none of their offsets, and adds it to
    the shape's own current ``a:off``/``a:ext``, read fresh from this copy of
    the file.
    """
    groups = _ancestor_groups(element)
    if not groups:
        return None

    scale_x, scale_y = 1.0, 1.0
    for group in groups:
        gx, gy = _group_scale(group)
        scale_x *= gx
        scale_y *= gy
    if not scale_x or not scale_y:
        raise MoveFailed("that group's coordinate space is degenerate")

    xfrm = None
    for path in _XFRM_PATHS:
        xfrm = element.find(path, _NS)
        if xfrm is not None:
            break
    if xfrm is None:
        raise MoveFailed(
            "this shape has no transform of its own inside its group, so "
            "there is nothing to adjust safely."
        )
    off = xfrm.find("a:off", _NS)
    ext = xfrm.find("a:ext", _NS)
    if off is None or ext is None:
        raise MoveFailed(
            "this shape's transform is incomplete, so there is nothing to "
            "adjust safely."
        )

    current_x, current_y = int(off.get("x") or 0), int(off.get("y") or 0)
    current_cx, current_cy = int(ext.get("cx") or 0), int(ext.get("cy") or 0)

    return (
        current_x + round(slide_dx_emu / scale_x),
        current_y + round(slide_dy_emu / scale_y),
        current_cx + round(slide_dcx_emu / scale_x),
        current_cy + round(slide_dcy_emu / scale_y),
    )


def _transform(
    element: etree._Element, x_emu: int, y_emu: int, cx_emu: int, cy_emu: int
) -> etree._Element:
    """This shape's ``a:xfrm``, created with its current whole box if absent.

    Takes all four numbers rather than just the pair its caller is about to
    write, because a transform that has to be created from nothing needs a
    complete, valid box the instant it exists -- an ``a:xfrm`` with an offset
    and no extent, or the reverse, is not valid OOXML. A move already knows
    the shape's current size for exactly this reason and a resize its current
    position, so each supplies the other's half and this writes both together;
    only the caller's own half is ever touched again afterwards.
    """
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
    offset = etree.SubElement(xfrm, f"{{{_A}}}off")
    offset.set("x", str(x_emu))
    offset.set("y", str(y_emu))
    extent = etree.SubElement(xfrm, f"{{{_A}}}ext")
    extent.set("cx", str(cx_emu))
    extent.set("cy", str(cy_emu))
    return xfrm


def _shape_in_slide(
    items: dict[str, bytes], slide_index: int, shape_id: int
) -> tuple[etree._Element, str, etree._Element]:
    """The slide's parsed root, its part name, and the named shape.

    Shared by every geometry writer, so "which slide is this really" and
    "which shape on it" are answered the same way regardless of which
    attribute of the transform is about to change. Finds the shape wherever
    it sits, including inside a group -- writing safely into a grouped
    shape's own coordinate space is :func:`_group_target`'s job, not this
    one's.
    """
    parts = _slide_parts(items)
    if not 1 <= slide_index <= len(parts):
        raise MoveFailed(f"this deck has no slide {slide_index}")
    part = parts[slide_index - 1]
    if part not in items:
        raise MoveFailed(f"slide {slide_index} points at {part}, which is not in the package")

    root = etree.fromstring(items[part])
    tree = root.find("p:cSld/p:spTree", _NS)
    if tree is None:
        raise MoveFailed(f"slide {slide_index} has no shape tree")

    return root, part, _shape_element_anywhere(tree, shape_id)


def _write_part(path: Path, items: dict[str, bytes], part: str, root: etree._Element) -> None:
    items[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, content in items.items():
            out.writestr(name, content)


def _apply_move(path: Path, fix: Fix) -> int:
    """Write one shape's offset, and touch nothing else in the package.

    Only ``a:off``'s ``x`` and ``y`` are assigned, in slide space for a shape
    with none to answer to and in its own group's space -- via
    :func:`_group_target` -- for one that does. The extent, the rotation, the
    flips and every other attribute on the transform are left exactly as they
    were, because the person moved the shape -- they did not resize it, turn it
    or mirror it, and a fix that changed more than it was asked to is the defect
    this module exists to avoid.
    """
    payload = fix.payload
    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}

    root, part, element = _shape_in_slide(
        items, int(payload["slide"]), int(payload["shape_id"])
    )
    x_emu, y_emu = int(payload["x_emu"]), int(payload["y_emu"])

    grouped = _group_target(
        element,
        slide_dx_emu=x_emu - int(payload["current_x_emu"]),
        slide_dy_emu=y_emu - int(payload["current_y_emu"]),
        slide_dcx_emu=0,
        slide_dcy_emu=0,
    )
    if grouped is not None:
        x_emu, y_emu, cx_emu, cy_emu = grouped
    else:
        cx_emu, cy_emu = int(payload["cx_emu"]), int(payload["cy_emu"])

    xfrm = _transform(element, x_emu, y_emu, cx_emu, cy_emu)
    offset = xfrm.find("a:off", _NS)
    if offset is None:
        offset = etree.Element(f"{{{_A}}}off")
        xfrm.insert(0, offset)  # a:off precedes a:ext; the schema fixes the order
    offset.set("x", str(x_emu))
    offset.set("y", str(y_emu))

    _write_part(path, items, part, root)
    return 1


def _apply_resize(path: Path, fix: Fix) -> int:
    """Write one shape's extent, and touch nothing else in the package.

    The mirror of :func:`_apply_move`: only ``a:ext``'s ``cx`` and ``cy`` are
    assigned, in the same coordinate space a move would use. The offset, the
    rotation, the flips and everything else on the transform are left exactly
    as they were, because the person resized the shape -- they did not move
    it -- and a fix that moved it as a side effect of resizing it would be
    exactly the defect :func:`_apply_move` avoids in the other direction.
    """
    payload = fix.payload
    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}

    root, part, element = _shape_in_slide(
        items, int(payload["slide"]), int(payload["shape_id"])
    )
    cx_emu, cy_emu = int(payload["cx_emu"]), int(payload["cy_emu"])

    grouped = _group_target(
        element,
        slide_dx_emu=0,
        slide_dy_emu=0,
        slide_dcx_emu=cx_emu - int(payload["current_cx_emu"]),
        slide_dcy_emu=cy_emu - int(payload["current_cy_emu"]),
    )
    if grouped is not None:
        x_emu, y_emu, cx_emu, cy_emu = grouped
    else:
        x_emu, y_emu = int(payload["x_emu"]), int(payload["y_emu"])

    xfrm = _transform(element, x_emu, y_emu, cx_emu, cy_emu)
    extent = xfrm.find("a:ext", _NS)
    if extent is None:
        extent = etree.SubElement(xfrm, f"{{{_A}}}ext")
    extent.set("cx", str(cx_emu))
    extent.set("cy", str(cy_emu))

    _write_part(path, items, part, root)
    return 1


def _apply_retext(path: Path, fix: Fix) -> int:
    """Replace one run's text, addressed by its exact position.

    Reads the paragraph and the run the same way the loader counts them --
    ``a:r``, ``a:br`` and ``a:fld`` together, in document order -- so an index
    the page took from :mod:`tieout_ui.canvas` names the same run here that it
    named there. A break or a field is refused rather than guessed at: a line
    break has no text of its own to replace, and a field's is PowerPoint's own
    rendering of a value TieOut does not own and would only overwrite until
    the field next updates.
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

    element = _shape_element_anywhere(tree, int(payload["shape_id"]))
    body = element.find("p:txBody", _NS)
    if body is None:
        raise MoveFailed("that shape has no text frame")

    paragraphs = body.findall("a:p", _NS)
    p_index = int(payload["paragraph"])
    if not 0 <= p_index < len(paragraphs):
        raise MoveFailed(f"paragraph {p_index} does not exist in that text frame")
    paragraph = paragraphs[p_index]

    runs = [
        child for child in paragraph if etree.QName(child).localname in ("r", "br", "fld")
    ]
    r_index = int(payload["run"])
    if not 0 <= r_index < len(runs):
        raise MoveFailed(f"run {r_index} does not exist in that paragraph")
    run = runs[r_index]
    if etree.QName(run).localname != "r":
        raise MoveFailed(
            "that is a line break or an auto-updating field, not editable text"
        )

    text_el = run.find("a:t", _NS)
    if text_el is None:
        text_el = etree.SubElement(run, f"{{{_A}}}t")
    new_text = payload["text"]
    text_el.text = new_text
    # A run PowerPoint would otherwise trim or collapse the padding from.
    if new_text != new_text.strip():
        text_el.set(_XML_SPACE, "preserve")

    _write_part(path, items, part, root)
    return 1


def _apply_recell(path: Path, fix: Fix) -> int:
    """Replace one run of one table cell, addressed by its exact position.

    The same discipline as :func:`_apply_retext` -- positional, never a
    substring match -- down a different path. A table's text is not in the
    shape's ``p:txBody``; it is in
    ``a:graphic/a:graphicData/a:tbl/a:tr[row]/a:tc[column]/a:txBody``, and a
    ``graphicFrame`` has no ``p:txBody`` at all. That is the whole reason this
    function exists rather than a flag on the other one.

    Rows and columns are counted as the loader counts them, so an index taken
    from :mod:`tieout.figures` names the same cell here. A merged cell still
    occupies its grid position in the XML, so indexing ``a:tc`` within
    ``a:tr`` stays aligned with the model's view of the grid.
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

    element = _shape_element_anywhere(tree, int(payload["shape_id"]))
    table = element.find(".//a:tbl", _NS)
    if table is None:
        raise MoveFailed("that shape is not a table")

    rows = table.findall("a:tr", _NS)
    row_index = int(payload["row"])
    if not 0 <= row_index < len(rows):
        raise MoveFailed(f"row {row_index} does not exist in that table")
    cells = rows[row_index].findall("a:tc", _NS)
    column_index = int(payload["column"])
    if not 0 <= column_index < len(cells):
        raise MoveFailed(f"column {column_index} does not exist in that row")

    body = cells[column_index].find("a:txBody", _NS)
    if body is None:
        raise MoveFailed("that cell has no text")

    paragraphs = body.findall("a:p", _NS)
    p_index = int(payload["paragraph"])
    if not 0 <= p_index < len(paragraphs):
        raise MoveFailed(f"paragraph {p_index} does not exist in that cell")

    runs = [
        child
        for child in paragraphs[p_index]
        if etree.QName(child).localname in ("r", "br", "fld")
    ]
    r_index = int(payload["run"])
    if not 0 <= r_index < len(runs):
        raise MoveFailed(f"run {r_index} does not exist in that cell")
    run = runs[r_index]
    if etree.QName(run).localname != "r":
        raise MoveFailed(
            "that is a line break or an auto-updating field, not editable text"
        )

    text_el = run.find("a:t", _NS)
    if text_el is None:
        text_el = etree.SubElement(run, f"{{{_A}}}t")
    new_text = str(payload["text"])
    text_el.text = new_text
    if new_text != new_text.strip():
        text_el.set(_XML_SPACE, "preserve")

    _write_part(path, items, part, root)
    return 1


_APPLIERS: Final[dict[str, Any]] = {
    MOVE_KIND: _apply_move,
    RESIZE_KIND: _apply_resize,
    RETEXT_KIND: _apply_retext,
    RECELL_KIND: _apply_recell,
    "colour": _apply_colour,
    "typeface": _apply_typeface,
    "metadata": _apply_metadata,
    "notes": _apply_notes,
    "comments": _apply_comments,
    "whitespace": _apply_text,
    "term": _apply_text,
    "quotes": _apply_text,
}
