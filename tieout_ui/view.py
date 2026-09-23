"""Serialising a profile and an audit for the browser.

Kept apart from the server so that what the page is shown is a pure function of
the model, testable without an HTTP client, and so that adding a field to the UI
cannot accidentally become a change to the audit.

One rule governs everything here: the browser is shown what the tool derived and
the evidence for it, never a value the tool guessed at. Every derived fact
carries its `why:` provenance and its confidence, because the point of the
confirmation step is that someone can disagree with a specific claim, and they
cannot disagree with a number that arrives without its reason.
"""

from __future__ import annotations

from typing import Any, Final

from tieout.model.deck import DeckModel, ShapeModel
from tieout.model.units import pt_to_emu
from tieout.profile.schema import Profile
from tieout.rules.base import SEVERITY_ORDER, AuditResult, Finding, load_all_rules
from tieout_fix import action_key, finding_key, group_geometry_refusal
from tieout_ui.edit import can_clear, editable

__all__ = [
    "MOVABLE_RULES",
    "audit_view",
    "find_shape",
    "movable",
    "profile_view",
    "rules_view",
]


#: The rules where moving the shape is the correction, so the page offers to.
#:
#: Narrower than the set of rules TieOut declines to fix, deliberately. LO-006
#: reports text overflowing its box and LO-007 a font size out of band: dragging
#: the shape elsewhere makes neither of them true, and a **Move it** button on
#: them would be an instruction to do something that does not work. BR-003 is
#: the same -- a distorted logo is the wrong *size*, and this editor does not
#: resize. What is left is the rules that are all about where a thing sits.
MOVABLE_RULES: Final[frozenset[str]] = frozenset(
    {"BR-002", "BR-008", "LO-001", "LO-002", "LO-003", "LO-004", "LO-005", "LO-008"}
)


def _fact(
    profile: Profile,
    path: str,
    label: str,
    value: Any,
    *,
    kind: str = "text",
) -> dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "kind": kind,
        "value": value,
        "why": profile.provenance.get(path, ""),
        "confidence": profile.confidence.get(path, "high"),
        # Asked here so the page only offers the control where it means
        # something. A tolerance resets to its default rather than vanishing,
        # and a required field with no default cannot be dropped at all.
        "droppable": can_clear(profile, path),
        # The controls to draw for retyping this value: one per editable member
        # for a margin set or a size band, one for a scalar, none at all for a
        # shape a form cannot carry. Resolved from the schema rather than listed
        # here, so a new field on the profile gets an editor without this file
        # having to hear about it.
        "edit": editable(profile, path),
    }


def profile_view(profile: Profile) -> dict[str, Any]:
    """Everything the confirmation step shows, grouped as a person reads it."""
    brand = profile.brand
    groups: list[dict[str, Any]] = []

    palette = [
        {"hex": value, "label": value}
        for value in brand.palette_hex
    ]
    colour_facts = [
        _fact(profile, "brand.palette_hex", "Palette", palette, kind="swatches"),
        _fact(
            profile,
            "brand.palette_tolerance_delta_e",
            "Colour tolerance (Delta-E)",
            brand.palette_tolerance_delta_e,
        ),
    ]
    if brand.palette_discarded:
        colour_facts.append(
            _fact(
                profile,
                "brand.palette_discarded",
                "Discarded as one-offs",
                [{"hex": value, "label": value} for value in brand.palette_discarded],
                kind="swatches",
            )
        )
    groups.append({"title": "Colour", "facts": colour_facts})

    type_facts = [
        _fact(profile, "brand.fonts.allowed", "Approved typefaces", brand.fonts.allowed,
              kind="list"),
    ]
    for role, band in sorted(brand.fonts.roles.items()):
        type_facts.append(
            _fact(
                profile,
                f"brand.fonts.roles.{role}",
                f"{role.replace('_', ' ')} size",
                _band(band),
            )
        )
    groups.append({"title": "Type", "facts": type_facts})

    logo_facts: list[dict[str, Any]] = []
    if brand.logo is not None:
        for archetype, box in sorted(
            brand.logo.per_archetype.items(), key=lambda item: item[0]
        ):
            logo_facts.append(
                _fact(
                    profile,
                    f"brand.logo.per_archetype.{archetype}",
                    f"Logo on {archetype.replace('_', ' ')}",
                    "exempt"
                    if box == "exempt"
                    else f"{box.left:.0f}, {box.top:.0f} · {box.width:.0f}×{box.height:.0f}pt",
                )
            )
    if logo_facts:
        groups.append({"title": "Logo", "facts": logo_facts})

    footer_facts: list[dict[str, Any]] = []
    page_number = brand.footer.page_number
    if page_number is not None:
        footer_facts.append(
            _fact(
                profile,
                "brand.footer.page_number",
                "Page numbers",
                f"pattern {page_number.regex} on {len(page_number.required_on)} archetype(s)",
            )
        )
    for index, entry in enumerate(brand.footer.boilerplate):
        footer_facts.append(
            _fact(
                profile,
                f"brand.footer.boilerplate.{index}",
                "Required text",
                entry.text,
            )
        )
    if footer_facts:
        groups.append({"title": "Footer", "facts": footer_facts})

    layout_facts: list[dict[str, Any]] = []
    for archetype, margins in sorted(profile.layout.safe_margin_pt.items()):
        layout_facts.append(
            _fact(
                profile,
                f"layout.safe_margin_pt.{archetype}",
                f"Safe margin on {archetype.replace('_', ' ')}",
                f"{margins.top:.0f} / {margins.right:.0f} / "
                f"{margins.bottom:.0f} / {margins.left:.0f}pt",
            )
        )
    grid = profile.layout.grid
    if grid.columns_pt or grid.rows_pt:
        layout_facts.append(
            _fact(
                profile,
                "layout.grid",
                "Alignment grid",
                f"{len(grid.columns_pt)} column line(s), {len(grid.rows_pt)} row line(s)",
            )
        )
    for element in profile.layout.recurring:
        layout_facts.append(
            _fact(
                profile,
                f"layout.recurring.{element.key}",
                f"Recurring: {element.key}",
                f"{element.box_pt.left:.0f}, {element.box_pt.top:.0f} "
                f"(support {element.support})",
            )
        )
    if layout_facts:
        groups.append({"title": "Layout", "facts": layout_facts})

    typography = profile.typography
    typography_facts = [
        _fact(profile, f"typography.{name}", label, getattr(typography, name))
        for name, label in (
            ("quotes", "Quote style"),
            ("title_case", "Title capitalisation"),
            ("bullet_terminal_punctuation", "Bullet punctuation"),
            ("date_format", "Date format"),
            ("currency_pattern", "Currency notation"),
        )
        if getattr(typography, name) is not None
    ]
    if typography.canon_terms:
        typography_facts.append(
            _fact(
                profile,
                "typography.canon_terms",
                "Canonical terms",
                sorted(typography.canon_terms),
                kind="list",
            )
        )
    if typography_facts:
        groups.append({"title": "Typography", "facts": typography_facts})

    return {
        "client": profile.client,
        "version": profile.version,
        "generated_at": profile.generated_at,
        "sources": profile.sources,
        "slide": {"width_pt": profile.slide.width_pt, "height_pt": profile.slide.height_pt},
        "archetypes": profile.archetypes,
        "groups": groups,
        "not_learned": [
            {"key": entry.key, "reason": entry.reason} for entry in profile.not_learned
        ],
        "questions": [
            {
                "id": question.id,
                "field_path": question.field_path,
                "question": question.question,
                "options": question.options,
                "default": question.default,
                "answered": question.answered,
                "answer": question.answer,
            }
            for question in profile.questions
        ],
        "disabled_rules": profile.rules.disabled,
    }


def _band(band: object) -> str:
    exact = getattr(band, "exact_pt", None)
    if exact:
        return ", ".join(f"{value:g}pt" for value in exact)
    low = getattr(band, "min_pt", None)
    high = getattr(band, "max_pt", None)
    if low is not None and high is not None:
        return f"{low:g}–{high:g}pt"
    return "not learned"


def find_shape(deck: DeckModel, slide_index: int, shape_id: int) -> ShapeModel | None:
    """The shape with this id on this slide, wherever it sits in the tree.

    Walks into groups as well, so a caller asking about a grouped shape gets the
    shape and its ``group_path`` rather than nothing -- the difference between
    telling someone *why* they cannot drag this one and appearing not to know it
    exists.
    """
    slide = deck.slide(slide_index)
    if slide is None:
        return None
    for shape in slide.all_shapes():
        if shape.ref.shape_id == shape_id:
            return shape
    return None


def movable(shape: ShapeModel) -> str:
    """Empty when this shape's position can be written, the reason when not.

    A shape inside a group stores its offset in the group's child space, which
    the group then translates and scales; the model reports it in slide space,
    so writing a slide-space number straight into a child-space attribute would
    put the shape somewhere neither the tool nor the person intended.
    ``tieout_fix`` converts into the group's own space instead of refusing
    outright, so the only shapes actually refused here are ones inside a group
    that itself rotates or mirrors its children -- a transform the conversion
    does not attempt -- or with no transform of its own to convert. Both are
    :func:`tieout_fix.group_geometry_refusal`'s call, kept in that package so
    this prediction can never disagree with what the write path actually does.
    """
    if not shape.ref.group_path:
        return ""
    return group_geometry_refusal(shape.raw_element)


def _move_block(finding: Finding, deck: DeckModel) -> dict[str, Any] | None:
    """What the canvas needs to pick this finding's shape up, or None.

    The geometry comes from the model rather than from ``finding.bbox_pt``,
    because they are not always the same box: LO-001 and LO-002 report the
    rotation-aware box a reader sees, and what a move writes is the stored
    offset. Dragging the visual box of a rotated shape and writing the result as
    its offset would move it by the wrong amount.

    EMU, as integers, for the reason given on :func:`tieout_fix.move_fix`: the
    page does its arithmetic in whole EMU so that a nudge out and a nudge back
    cancel exactly.
    """
    ref = finding.where
    if isinstance(ref, int):
        return None
    shape = find_shape(deck, ref.slide_index, ref.shape_id)
    if shape is None:
        return None
    refused = movable(shape)
    block: dict[str, Any] = {
        "shape_id": ref.shape_id,
        "shape": ref.display_name,
        "refused": refused,
        "left_pt": shape.left_pt,
        "top_pt": shape.top_pt,
        "width_pt": shape.width_pt,
        "height_pt": shape.height_pt,
        "rotation": shape.rotation,
    }
    if not refused:
        block.update(
            x_emu=pt_to_emu(shape.left_pt),
            y_emu=pt_to_emu(shape.top_pt),
            cx_emu=pt_to_emu(shape.width_pt),
            cy_emu=pt_to_emu(shape.height_pt),
        )
    return block


def _figure_block(finding: Finding) -> dict[str, Any] | None:
    """What the page needs to act on a tie-out finding, or None.

    PLAN.md §5.5's split, carried to the browser unchanged. The rule already
    decided which of the two this is and why; nothing is re-derived here,
    because a second opinion about whether a figure is derived is a second
    place for the two to disagree.

    ``fix`` findings do not need this block to get a button -- ``plan_fixes``
    gives them the ordinary **Fix it** every other correctable rule uses. They
    carry it anyway so the page can show the replacement beside the button,
    and so a refusal has somewhere to be said out loud.
    """
    correction = finding.correction
    if correction is None:
        return None
    return {
        "kind": correction.kind,
        "source": correction.source,
        "shape_id": correction.shape_id,
        "uid": correction.uid,
        "row": correction.address[0] if correction.source == "table" else None,
        "column": correction.address[1] if correction.source == "table" else None,
        "paragraph": (
            correction.cell_paragraph
            if correction.source == "table"
            else correction.address[0]
        ),
        "run": (
            correction.cell_run if correction.source == "table" else correction.address[1]
        ),
        "current": correction.current,
        "replacement": correction.replacement,
        "counterpart": correction.counterpart,
        "counterpart_slide": correction.counterpart_slide,
        "refused": correction.refused,
    }


def audit_view(result: AuditResult, deck: DeckModel) -> dict[str, Any]:
    """The audit, arranged slide by slide.

    Slide-wise because that is the order a deck gets fixed in: someone opens
    slide 7, fixes everything wrong with slide 7, and moves on. A report grouped
    by rule makes them open slide 7 eleven times.
    """
    by_slide: dict[int, list[dict[str, Any]]] = {}
    for finding in sorted(result.findings, key=lambda f: f.sort_key):
        by_slide.setdefault(finding.slide_index, []).append(
            {
                # Stable across a re-audit, so the page can say which findings
                # a correction cleared and which it exposed.
                "id": finding_key(finding),
                "rule_id": finding.rule_id,
                "category": finding.category,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "shape": finding.shape_name,
                "message": finding.message,
                "measured": finding.measured,
                "expected": finding.expected,
                "why": finding.expected_provenance,
                "remedy": finding.remedy,
                "bbox_pt": finding.bbox_pt,
                "move": _move_block(finding, deck),
                "figure": _figure_block(finding),
            }
        )

    unchecked: dict[int, list[dict[str, str]]] = {}
    for entry in result.unchecked:
        unchecked.setdefault(entry.slide_index, []).append(
            {"rule_id": entry.rule_id, "reason": entry.reason}
        )

    slides = [
        {
            "index": slide.index,
            "archetype": slide.archetype,
            "archetype_confidence": slide.archetype_confidence,
            "title": _slide_title(slide),
            "hidden": slide.is_hidden,
            "findings": by_slide.get(slide.index, []),
            "unchecked": unchecked.get(slide.index, []),
            "worst": _worst(by_slide.get(slide.index, [])),
        }
        for slide in deck.slides
    ]

    return {
        "deck": result.deck_path,
        "client": result.client,
        "slide_count": result.slide_count,
        "summary": result.summary,
        "verdict": _verdict(result.summary),
        "actions": _actions(slides),
        "slides": slides,
        "rules_run": sorted(result.rules_run),
        "rules_skipped": [
            {"rule_id": entry.rule_id, "reason": entry.reason}
            for entry in result.rules_skipped
        ],
        "suppressed": len(result.suppressed),
    }


#: What each severity means for the decision the reader is actually making.
#: "24 points to address" does not answer "can I send this", which is the only
#: question anyone opens this tool with at eleven at night.
_VERDICTS: Final[dict[str, tuple[str, str]]] = {
    "block": (
        "Not ready to send",
        "Blockers are defects that embarrass whoever the deck goes to: a leaked "
        "name, a draft marker, the wrong canvas. Clear these first.",
    ),
    "fix": (
        "Fix before sending",
        "Nothing here leaks or misleads, but each one is visible to the reader "
        "as a mistake in the deck.",
    ),
    "polish": (
        "Ready to send",
        "What is left is polish. None of it would be noticed by a reader who was "
        "not looking for it.",
    ),
    "clear": (
        "Ready to send",
        "This deck matches the house style it was checked against.",
    ),
}


def _verdict(summary: dict[str, int]) -> dict[str, str]:
    """The send-or-not call, from the severities present."""
    if summary.get("blocker"):
        state = "block"
    elif summary.get("major"):
        state = "fix"
    elif summary.get("minor") or summary.get("info"):
        state = "polish"
    else:
        state = "clear"
    headline, detail = _VERDICTS[state]
    return {"state": state, "headline": headline, "detail": detail}


def _actions(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Findings collapsed to the decisions behind them.

    One off-palette gold used on four slides is one decision and four findings.
    Listed slide by slide it reads as four problems, and a reader who fixes the
    theme colour once then has to work out that the other three are the same
    thing. So findings are grouped by what a person would actually do about
    them: the rule, the expectation it failed and the fix.

    The instances are kept and shown beneath, because the slide-by-slide order
    is still how a deck gets corrected -- this is a second way in, not a
    replacement.
    """
    summaries = {rule_id: rule.summary for rule_id, rule in load_all_rules().items()}
    grouped: dict[str, dict[str, Any]] = {}

    for slide in slides:
        for finding in slide["findings"]:
            # Keyed on the fix, because the fix is what makes several findings
            # one job. Six shapes each a point off six different grid lines
            # share a remedy and are one pass with the mouse; two off-palette
            # colours do not and are two. Where a rule states no remedy, the
            # expectation stands in, so unrelated findings cannot collapse
            # together on an empty string.
            # The same string ``tieout_fix`` keys a correction on, so the page
            # can put the button that applies it beside the sentence describing
            # it without either side inventing an identity for the other.
            key = action_key(
                finding["rule_id"],
                finding["remedy"],
                finding["expected"],
                finding["message"],
            )
            action = grouped.get(key)
            if action is None:
                action = grouped[key] = {
                    "key": key,
                    "rule_id": finding["rule_id"],
                    "category": finding["category"],
                    "severity": finding["severity"],
                    "title": summaries.get(finding["rule_id"], finding["message"]),
                    "measured": finding["measured"],
                    "expected": finding["expected"],
                    "remedy": finding["remedy"],
                    "why": finding["why"],
                    "slides": [],
                    "instances": [],
                }
            if slide["index"] not in action["slides"]:
                action["slides"].append(slide["index"])
            action["instances"].append(
                {
                    "id": finding["id"],
                    "slide": slide["index"],
                    "shape": finding["shape"],
                    "message": finding["message"],
                    "measured": finding["measured"],
                    "expected": finding["expected"],
                    "bbox_pt": finding["bbox_pt"],
                    "move": finding["move"],
                    "figure": finding["figure"],
                }
            )

    actions = list(grouped.values())
    # Worst first, then the one affecting most slides: the order in which a
    # person with an hour before the deck goes out should work through them.
    actions.sort(
        key=lambda a: (
            SEVERITY_ORDER.get(a["severity"], 9),
            -len(a["instances"]),
            a["rule_id"],
        )
    )
    for action in actions:
        action["count"] = len(action["instances"])
        # Whether the page offers to open the position editor on this group. Both
        # halves are needed: the rule has to be one moving the shape answers, and
        # at least one of its shapes has to be one this can actually write.
        action["movable"] = action["rule_id"] in MOVABLE_RULES and any(
            instance["move"] and not instance["move"]["refused"]
            for instance in action["instances"]
        )
        # Whether the page offers **Edit it**: two *stated* figures disagree,
        # which of them is right is a judgement about the deal, and the editor
        # can reach the run that holds one of them. Deliberately not "fixable":
        # a derived figure gets the ordinary Fix it button, and offering both
        # on one finding would blur the only distinction this is drawing.
        action["editable"] = any(
            instance["figure"]
            and instance["figure"]["kind"] == "edit"
            and not instance["figure"]["refused"]
            for instance in action["instances"]
        )
        # A measurement or an expectation on the group header is only true if
        # every instance shares it. Six shapes off six different grid lines
        # have six expectations, and printing the first one at the top would
        # be a wrong number stated confidently.
        for field in ("measured", "expected"):
            values = {instance[field] for instance in action["instances"]}
            action[field] = values.pop() if len(values) == 1 else None
    return actions


def _slide_title(slide: object) -> str:
    """How the review note names a slide.

    Asks the model rather than guessing. This used to take the title placeholder
    and otherwise the first text shape in document order, which is the lockup's
    monogram on any deck whose logo is drawn -- the client deck's note read
    "Slide 1 — H" and "Slide 12 — H" -- and the eyebrow everywhere else, so a
    slide headed "Important Notice" was named "INTRODUCTION".

    `SlideModel.title_text` is what every rule means by the title, and a reader
    comparing the note against the deck is entitled to the same answer. The old
    walk stays as the fallback for a slide that has no title at all.
    """
    title = getattr(slide, "title_text", "")
    if title:
        return " ".join(title.split())[:90]
    for shape in getattr(slide, "text_shapes", ()):
        text = " ".join(shape.text.split())
        if text:
            return text[:90]
    return ""


def _worst(findings: list[dict[str, Any]]) -> str | None:
    if not findings:
        return None
    worst = min(findings, key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    return str(worst["severity"])


def rules_view() -> list[dict[str, Any]]:
    """The rule catalogue, so the page can name a rule without hardcoding it."""
    return [
        {
            "rule_id": rule_id,
            "category": rule.category,
            "severity": rule.severity,
            "summary": rule.summary,
            "default_enabled": rule.default_enabled,
        }
        for rule_id, rule in sorted(load_all_rules().items())
    ]
