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

from tieout.model.deck import DeckModel
from tieout.profile.schema import Profile
from tieout.rules.base import SEVERITY_ORDER, AuditResult, load_all_rules
from tieout_fix import action_key
from tieout_ui.edit import can_clear, editable

__all__ = ["audit_view", "profile_view", "rules_view"]


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
                    "slide": slide["index"],
                    "shape": finding["shape"],
                    "message": finding["message"],
                    "measured": finding["measured"],
                    "expected": finding["expected"],
                    "bbox_pt": finding["bbox_pt"],
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
        # A measurement or an expectation on the group header is only true if
        # every instance shares it. Six shapes off six different grid lines
        # have six expectations, and printing the first one at the top would
        # be a wrong number stated confidently.
        for field in ("measured", "expected"):
            values = {instance[field] for instance in action["instances"]}
            action[field] = values.pop() if len(values) == 1 else None
    return actions


def _slide_title(slide: object) -> str:
    shapes = getattr(slide, "text_shapes", ())
    for shape in shapes:
        if shape.is_placeholder and (shape.placeholder_type or "").startswith("title"):
            return " ".join(shape.text.split())[:90]
    for shape in shapes:
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
