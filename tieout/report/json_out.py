"""The JSON report.

A stable schema, because this is the output another tool consumes: a pre-send
gate in a deployment pipeline, a dashboard, a spreadsheet. Renaming a key here
breaks somebody's script, so the key set is fixed and additive.

``unchecked`` and ``rules_skipped`` are top-level rather than buried, for the
same reason they lead the console footer: a consumer must be able to tell a clean
deck from an unexamined one.

Findings are sorted here rather than relying on the caller. The engine already
sorts, but a consumer parsing the document has only the document to go on, so the
ordering the schema promises is made true at the point of serialisation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from tieout.model.deck import ShapeRef
from tieout.rules.base import AuditResult

#: Bumped only for a breaking change to the key set.
SCHEMA_VERSION: Final[int] = 1


def build(result: AuditResult) -> dict[str, Any]:
    """The report as a plain dictionary."""
    return {
        "schema_version": SCHEMA_VERSION,
        "deck": result.deck_path,
        "client": result.client,
        "profile_version": result.profile_version,
        "generated_at": result.generated_at,
        "slide_count": result.slide_count,
        "summary": result.summary,
        "findings": [
            _finding(finding)
            for finding in sorted(result.findings, key=lambda f: f.sort_key)
        ],
        "unchecked": [
            {
                "rule_id": entry.rule_id,
                "slide_index": entry.slide_index,
                "shape": _where(entry.where),
                "reason": entry.reason,
            }
            for entry in result.unchecked
        ],
        "rules_skipped": [
            {"rule_id": entry.rule_id, "reason": entry.reason, "failed": entry.failed}
            for entry in result.rules_skipped
        ],
        "rules_run": list(result.rules_run),
        "suppressed": [
            _finding(finding)
            for finding in sorted(result.suppressed, key=lambda f: f.sort_key)
        ],
    }


def _finding(finding: Any) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "category": finding.category,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "slide_index": finding.slide_index,
        "shape": _where(finding.where),
        "message": finding.message,
        "measured": finding.measured,
        "expected": finding.expected,
        "expected_provenance": finding.expected_provenance,
        "bbox_pt": list(finding.bbox_pt) if finding.bbox_pt else None,
    }


def _where(where: ShapeRef | int) -> dict[str, Any] | None:
    """Shape identity, or None when the finding is about a slide or the deck."""
    if not isinstance(where, ShapeRef):
        return None
    return {
        "shape_id": where.shape_id,
        "name": where.name,
        "group_path": list(where.group_path),
        "display_name": where.display_name,
    }


def render(result: AuditResult, *, indent: int = 2) -> str:
    return json.dumps(build(result), indent=indent, sort_keys=False) + "\n"


def write(result: AuditResult, path: str | Path, *, indent: int = 2) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(result, indent=indent), encoding="utf-8")
    return target
