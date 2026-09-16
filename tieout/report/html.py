"""The HTML report.

One self-contained file. No CDN references, no external fonts, no network access
at render time or at view time, because the tool is specified to run air-gapped
and this is the artefact most likely to be emailed out of the firm.

Every finding carries the provenance string, which is what makes the report
persuasive rather than arbitrary: the reader can see the expectation came from
their own reference deck. A report that merely asserts "the logo should be at
852pt" gets argued with; one that says "on 12 of your content slides it is" does
not.

Each slide reserves a fixed-aspect ``.thumb`` element and each finding carries a
``data-bbox`` attribute, so slide images and overlays can be added later by
populating the box and reading the attributes, with no change to this module or
the template.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tieout.model.deck import DeckModel
from tieout.rules.base import SEVERITY_ORDER, AuditResult, Finding

_TEMPLATE_DIR: Final[Path] = Path(__file__).parent / "templates"
_TEMPLATE_NAME: Final[str] = "report.html.j2"


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render(result: AuditResult, deck: DeckModel | None = None) -> str:
    """Render the report to a string."""
    grouped = result.findings_by_slide()
    slides = _slides(result, deck, grouped)

    template = _environment().get_template(_TEMPLATE_NAME)
    return template.render(
        result=result,
        deck_name=Path(result.deck_path).name,
        summary=result.summary,
        severities=sorted(SEVERITY_ORDER, key=lambda name: SEVERITY_ORDER[name]),
        slides=slides,
        slides_with_findings=[slide for slide in slides if slide["findings"]],
        unchecked=_unchecked(result),
    )


def write(result: AuditResult, path: str | Path, deck: DeckModel | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(result, deck), encoding="utf-8")
    return target


def _slides(
    result: AuditResult,
    deck: DeckModel | None,
    grouped: dict[int, list[Finding]],
) -> list[dict[str, Any]]:
    """Every slide in the deck, so the sidebar shows clean slides too.

    A sidebar listing only the slides with findings hides how much of the deck
    was examined, which is the same failure as omitting the unchecked list.
    """
    indices = sorted(
        {slide.index for slide in deck.slides} if deck else set(grouped)
        or set(grouped)
    )
    if not indices:
        indices = sorted(grouped)

    out: list[dict[str, Any]] = []
    for index in indices:
        findings = grouped.get(index, [])
        slide = deck.slide(index) if deck else None
        out.append(
            {
                "index": index,
                "archetype": slide.archetype if slide else "",
                "width_pt": round(slide.width_pt, 2) if slide else 0,
                "height_pt": round(slide.height_pt, 2) if slide else 0,
                "findings": findings,
                "counts": _counts(findings),
            }
        )
    return out


def _counts(findings: list[Finding]) -> list[tuple[str, int]]:
    tally: dict[str, int] = {}
    for finding in findings:
        tally[finding.severity] = tally.get(finding.severity, 0) + 1
    return [
        (severity, tally[severity])
        for severity in sorted(SEVERITY_ORDER, key=lambda s: SEVERITY_ORDER[s])
        if severity in tally
    ]


def _unchecked(result: AuditResult) -> list[dict[str, str]]:
    """Unchecked entries condensed to one row per rule and reason."""
    grouped: dict[tuple[str, str], list[int]] = {}
    for entry in result.unchecked:
        grouped.setdefault((entry.rule_id, entry.reason), []).append(entry.slide_index)
    rows: list[dict[str, str]] = []
    for (rule_id, reason), slides in sorted(grouped.items()):
        unique = sorted(set(slides))
        where = (
            f"slide {unique[0]}"
            if len(unique) == 1
            else f"{len(slides)} shapes on {len(unique)} slides"
        )
        rows.append({"rule_id": rule_id, "reason": reason, "where": where})
    return rows
