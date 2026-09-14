"""The console report.

Grouped by slide, then by severity, because that is the order in which someone
fixes a deck: they open slide 7, fix everything on it, and move on. Grouping by
rule would be tidier and useless.

The footer is not decoration. It states what was skipped and what could not be
checked, so the reader can tell "nothing wrong" from "nothing looked at" -- the
distinction that decides whether the report can be trusted as a pre-send gate.
"""

from __future__ import annotations

from typing import Final

from rich.box import SIMPLE_HEAD
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tieout.rules.base import SEVERITY_GLYPHS, SEVERITY_ORDER, AuditResult, Finding

_SEVERITY_STYLES: Final[dict[str, str]] = {
    "blocker": "bold red",
    "major": "bold yellow",
    "minor": "cyan",
    "info": "dim",
}

#: Longest message rendered before truncation. Wide enough for a full sentence,
#: narrow enough that the measured/expected column stays on screen.
_MESSAGE_WIDTH: Final[int] = 62


def render(
    result: AuditResult,
    *,
    console: Console | None = None,
    quiet: bool = False,
    show_provenance: bool = True,
) -> None:
    """Print the audit to the console."""
    output = console or Console()

    if quiet:
        _summary_line(result, output)
        return

    output.print()
    output.print(
        Panel(
            _header_text(result),
            title="tieout",
            border_style="blue",
            expand=False,
        )
    )

    grouped = result.findings_by_slide()
    if not grouped:
        output.print()
        output.print("[bold green]No findings.[/bold green]")
    for slide_index in sorted(grouped):
        output.print()
        output.print(_slide_table(slide_index, grouped[slide_index], show_provenance))

    output.print()
    _footer(result, output)


def _header_text(result: AuditResult) -> Text:
    text = Text()
    text.append("deck    ", style="dim")
    text.append(f"{result.deck_path}\n")
    text.append("client  ", style="dim")
    text.append(f"{result.client}  ")
    text.append("profile ", style="dim")
    text.append(f"v{result.profile_version}  ")
    text.append("slides  ", style="dim")
    text.append(str(result.slide_count))
    return text


def _slide_table(
    slide_index: int, findings: list[Finding], show_provenance: bool
) -> Table:
    table = Table(
        box=SIMPLE_HEAD,
        title=f"slide {slide_index}",
        title_justify="left",
        title_style="bold",
        pad_edge=False,
        show_edge=False,
    )
    table.add_column("", width=1, no_wrap=True)
    table.add_column("rule", width=6, no_wrap=True, style="dim")
    table.add_column("shape", width=22, overflow="ellipsis")
    table.add_column("message", width=_MESSAGE_WIDTH)
    table.add_column("measured → expected", overflow="fold")

    for finding in sorted(findings, key=lambda f: f.sort_key):
        style = _SEVERITY_STYLES.get(finding.severity, "")
        message = Text(finding.message, style=style if finding.severity == "blocker" else "")
        if show_provenance and finding.expected_provenance:
            message.append(f"\n{finding.expected_provenance}", style="dim italic")
        table.add_row(
            Text(SEVERITY_GLYPHS.get(finding.severity, "?"), style=style),
            finding.rule_id,
            finding.shape_name or "—",
            message,
            Text(finding.delta or "—", style="dim"),
        )
    return table


def _footer(result: AuditResult, console: Console) -> None:
    _summary_line(result, console)

    if result.suppressed:
        console.print(
            f"[dim]{len(result.suppressed)} finding(s) suppressed by "
            f"accepted entries.[/dim]"
        )

    if result.rules_skipped:
        console.print()
        console.print("[bold]rules not run[/bold]")
        for skipped in result.rules_skipped:
            console.print(
                Text(f"  {skipped.rule_id}  {skipped.reason}", style="dim")
            )

    if result.unchecked:
        console.print()
        console.print(
            f"[bold]not checked[/bold] [dim]({len(result.unchecked)} shape(s))[/dim]"
        )
        for entry in _condense_unchecked(result):
            console.print(Text(f"  {entry}", style="dim"))


def _condense_unchecked(result: AuditResult) -> list[str]:
    """One line per rule and reason, with a count.

    A deck with a font TieOut cannot resolve produces one unchecked entry per
    shape, and printing two hundred of them buries the summary that matters.
    """
    grouped: dict[tuple[str, str], list[int]] = {}
    for entry in result.unchecked:
        grouped.setdefault((entry.rule_id, entry.reason), []).append(entry.slide_index)
    lines: list[str] = []
    for (rule_id, reason), slides in sorted(grouped.items()):
        unique = sorted(set(slides))
        where = (
            f"slide {unique[0]}"
            if len(unique) == 1
            else f"{len(slides)} shapes on {len(unique)} slides"
        )
        lines.append(f"{rule_id}  {reason} ({where})")
    return lines


def _summary_line(result: AuditResult, console: Console) -> None:
    summary = result.summary
    if not result.findings:
        console.print(
            f"[bold green]0 findings[/bold green] from "
            f"{len(result.rules_run)} rules across {result.slide_count} slides."
        )
        return
    parts = [
        f"[{_SEVERITY_STYLES[name]}]{summary[name]} {name}[/{_SEVERITY_STYLES[name]}]"
        for name in sorted(SEVERITY_ORDER, key=lambda s: SEVERITY_ORDER[s])
        if summary.get(name)
    ]
    console.print(
        f"[bold]{summary['total']} finding(s)[/bold]: "
        + ", ".join(parts)
        + f" — from {len(result.rules_run)} rules across "
        f"{result.slide_count} slides."
    )


def render_rules_table(console: Console | None = None, client: str = "") -> None:
    """``tieout rules``: every rule, its state, severity and where it comes from."""
    from tieout.rules.base import load_all_rules

    output = console or Console()
    registry = load_all_rules()

    table = Table(box=SIMPLE_HEAD, title=f"tieout rules{f' for {client}' if client else ''}")
    table.add_column("rule", no_wrap=True)
    table.add_column("category", no_wrap=True)
    table.add_column("severity", no_wrap=True)
    table.add_column("default", no_wrap=True)
    table.add_column("what it measures")
    table.add_column("expectation from", overflow="fold", style="dim")

    for rule_id in sorted(registry):
        rule = registry[rule_id]
        table.add_row(
            rule_id,
            rule.category,
            Text(rule.severity, style=_SEVERITY_STYLES.get(rule.severity, "")),
            "on" if rule.default_enabled else Text("off", style="dim"),
            rule.summary,
            ", ".join(rule.requires) or "not learned, fixed behaviour",
        )
    output.print(table)
