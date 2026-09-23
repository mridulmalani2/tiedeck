"""``tieout-review`` — the optional semantic layer, as its own command.

A deliberate choice: this is a separate console script rather than a
``tieout review`` subcommand. ``tieout``'s guarantee is that no runtime module
in the package can reach a network, and it is enforced by walking the syntax
tree of every one of them. A subcommand would mean ``tieout.cli`` importing this
package, which would make that walk a statement about an import guard instead of
a statement about the code. The extra hyphen is worth it.

Two commands:

``tieout-review redact DECK``
    Shows exactly what would be sent, and what could not be cleared. Offline,
    and needs no key. This is the command to run first, and the one to run
    before believing anything else here.

``tieout-review check DECK``
    The full deterministic audit plus the semantic pass, in one report.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from tieout.cli import EXIT_ERROR, EXIT_FINDINGS
from tieout.model.loader import DeckLoadError, load_deck
from tieout.profile.loader import ProfileError, load_for_client, load_suppressions
from tieout.report import console as console_report
from tieout.report import html as html_report
from tieout.report import json_out
from tieout.rules.base import AuditResult, clear_caches, run_rules
from tieout_review.outbound import OutboundLogError, outbound_log_path
from tieout_review.redact import Redacted
from tieout_review.review import (
    ApprovalMismatch,
    Prepared,
    RedactionFailed,
    RedactionHeld,
    ResponseError,
    prepare,
    send,
)
from tieout_review.terms import blocklist_from_file, blocklist_from_text, iter_sources

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tieout_review.client import ReviewClient

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Optional LLM content review for TieOut. Every identifying term is "
        "replaced before anything leaves this machine, and nothing is sent "
        "while the redaction has items outstanding."
    ),
)

_out = Console()
_err = Console(stderr=True)

_FORBID_HELP = (
    "Comma-separated terms to redact, e.g. "
    '--forbid "Meridian Capital,Project Atlas,Jane Okafor".'
)


def _fail(message: str) -> None:
    _err.print(f"[bold red]error[/bold red] {message}")
    raise typer.Exit(EXIT_ERROR)


def _prepare(
    deck_path: Path,
    client: str,
    profile: Path | None,
    forbid: str | None,
    forbid_file: Path | None,
    include_notes: bool,
) -> Prepared:
    blocklist = blocklist_from_text(forbid)
    if forbid_file is not None:
        if not forbid_file.is_file():
            _fail(f"no such blocklist file: {forbid_file}")
        blocklist.extend(blocklist_from_file(forbid_file))

    loaded_profile = None
    try:
        if client or profile is not None:
            loaded_profile = load_for_client(client, profile)
        deck = load_deck(deck_path)
    except (ProfileError, DeckLoadError) as exc:
        _fail(str(exc))
        raise AssertionError("unreachable") from exc  # pragma: no cover

    return prepare(
        deck,
        loaded_profile,
        forbidden=blocklist,
        allowlist=[],
        include_notes=include_notes,
    )


def _render_plan(prepared: Prepared, *, show_payload: bool) -> None:
    plan = prepared.plan
    _out.print()
    _out.print(
        f"[bold]{len(plan.redactions)}[/bold] term(s) redacted across "
        f"[bold]{prepared.characters:,}[/bold] characters from "
        f"{prepared.slide_count} slides."
    )

    if plan.redactions:
        table = Table(title="redacted", title_justify="left", title_style="bold", pad_edge=False)
        table.add_column("placeholder", no_wrap=True)
        table.add_column("kind", no_wrap=True)
        table.add_column("uses", justify="right", no_wrap=True)
        table.add_column("original")
        table.add_column("from", style="dim")
        for entry in plan.redactions:
            table.add_row(
                entry.placeholder,
                entry.kind,
                str(entry.occurrences),
                entry.original,
                entry.source,
            )
        _out.print(table)

    sources = list(iter_sources(prepared.terms))
    if sources:
        _out.print("[dim]term list assembled from:[/dim]")
        for name, kind, count in sources:
            _out.print(f"  [dim]{name}[/dim]  {count} {kind} term(s)")

    _render_residuals(plan)
    _out.print(
        f"\n[dim]approval digest[/dim] [bold]{prepared.digest}[/bold]\n"
        "[dim]Names this exact payload and this exact residual list. Pass it to "
        "[bold]check --approve[/bold] and the send is refused if either has moved "
        "since.[/dim]"
    )

    if show_payload:
        _out.print()
        _out.print("[bold]payload as it would be sent[/bold]")
        _out.print(Text(plan.text), markup=False, highlight=False)


def _render_residuals(plan: Redacted) -> None:
    if plan.is_clear:
        _out.print("\n[bold green]Nothing outstanding.[/bold green]")
        return
    _out.print()
    table = Table(
        title=f"could not be cleared ({len(plan.residuals)})",
        title_justify="left",
        title_style="bold yellow",
        pad_edge=False,
    )
    table.add_column("line", justify="right", no_wrap=True)
    table.add_column("uses", justify="right", no_wrap=True)
    table.add_column("text")
    table.add_column("why", style="dim")
    for residual in plan.residuals:
        table.add_row(
            str(residual.first_line),
            str(residual.occurrences),
            residual.text,
            residual.reason,
        )
    _out.print(table)
    _out.print(
        "\nEach of these is a judgement only you can make. Add the ones that "
        "identify someone to [bold]--forbid[/bold] and run again; pass "
        "[bold]--yes[/bold] once the list is one you are content to send."
    )


@app.command()
def redact(
    deck_path: Annotated[Path, typer.Argument(help="The deck to inspect.")],
    client: Annotated[str, typer.Option("--client", help="Client name.")] = "",
    profile: Annotated[
        Path | None, typer.Option("--profile", help="Use this profile instead.")
    ] = None,
    forbid: Annotated[str | None, typer.Option("--forbid", help=_FORBID_HELP)] = None,
    forbid_file: Annotated[
        Path | None,
        typer.Option("--forbid-file", help="A file of terms, one per line; # comments."),
    ] = None,
    include_notes: Annotated[
        bool,
        typer.Option("--include-notes", help="Include speaker notes. Off by default."),
    ] = False,
    show_payload: Annotated[
        bool, typer.Option("--show-payload", help="Print the redacted text in full.")
    ] = False,
) -> None:
    """Show what would be sent, and what could not be cleared. Sends nothing."""
    prepared = _prepare(deck_path, client, profile, forbid, forbid_file, include_notes)
    _render_plan(prepared, show_payload=show_payload)
    if not prepared.plan.is_clear:
        raise typer.Exit(EXIT_FINDINGS)


@app.command()
def check(
    deck_path: Annotated[Path, typer.Argument(help="The deck to audit.")],
    client: Annotated[str, typer.Option("--client", help="Client name.")] = "",
    profile: Annotated[
        Path | None, typer.Option("--profile", help="Use this profile instead.")
    ] = None,
    forbid: Annotated[str | None, typer.Option("--forbid", help=_FORBID_HELP)] = None,
    forbid_file: Annotated[
        Path | None,
        typer.Option("--forbid-file", help="A file of terms, one per line; # comments."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Send even though items could not be cleared. Read them first.",
        ),
    ] = False,
    approve: Annotated[
        str | None,
        typer.Option(
            "--approve",
            help=(
                "The digest printed by 'redact', naming the residual list you read. "
                "Refuses if the deck or the term list has moved since."
            ),
        ),
    ] = None,
    include_notes: Annotated[
        bool, typer.Option("--include-notes", help="Include speaker notes. Off by default.")
    ] = False,
    model: Annotated[
        str | None, typer.Option("--model", help="Override the model.")
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="table, json or html.")
    ] = "table",
    out: Annotated[Path | None, typer.Option("--out", help="Write the report here.")] = None,
    fail_on: Annotated[
        str, typer.Option("--fail-on", help="Exit 1 at this severity or worse.")
    ] = "blocker",
    fail_on_semantic: Annotated[
        bool,
        typer.Option(
            "--fail-on-semantic",
            help="Let semantic findings drive the exit code too. Off by default.",
        ),
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Print only the summary.")] = False,
) -> None:
    """Run the full deterministic audit and a semantic pass, in one report."""
    if not client and profile is None:
        _fail("--client NAME or --profile PATH is required")
    if output_format not in ("table", "json", "html"):
        _fail(f"unknown --format {output_format!r}: use table, json or html")

    prepared = _prepare(deck_path, client, profile, forbid, forbid_file, include_notes)
    if not prepared.plan.is_clear and not (yes or approve):
        _render_plan(prepared, show_payload=False)
        _fail(
            "nothing has been sent. Review the list above, then re-run with "
            "--approve DIGEST, or with --yes to accept whatever the list says now"
        )

    try:
        prepared = _approved(prepared, yes=yes, approve=approve)
    except ApprovalMismatch as exc:
        _fail(str(exc))
        return

    review_client = _build_client(model)

    try:
        outcome = send(prepared, review_client)
    except RedactionHeld as exc:  # pragma: no cover - guarded above
        _fail(str(exc))
        return
    except (OutboundLogError, RedactionFailed, ResponseError) as exc:
        _fail(str(exc))
        return
    except Exception as exc:
        _fail(str(exc))
        return

    result = _audit(deck_path, client, profile)
    result.findings.extend(outcome.findings)
    result.rules_run.extend(sorted({finding.rule_id for finding in outcome.findings}))

    _emit(result, deck_path, client, profile, output_format, out, quiet=quiet)

    if not quiet:
        # On stderr, not stdout. `--format json` writes the report to stdout when
        # no --out is given, and a diagnostic line appended to it makes the
        # report unparseable — which is exactly the pipeline this format is for.
        _err.print(
            f"\n[dim]semantic pass: {len(outcome.findings)} finding(s) from "
            f"{outcome.model}, {len(outcome.plan.redactions)} term(s) withheld, "
            f"{outcome.characters_sent:,} characters sent"
            f"{_usage(outcome.usage)}[/dim]"
        )
        _err.print(f"[dim]recorded in {outbound_log_path()}[/dim]")
        if outcome.dropped:
            _err.print(
                f"[dim]{len(outcome.dropped)} answer(s) discarded as unreadable: "
                + "; ".join(outcome.dropped[:3])
                + "[/dim]"
            )

    if result.exceeds(fail_on, include_non_gating=fail_on_semantic):
        raise typer.Exit(EXIT_FINDINGS)


def _approved(prepared: Prepared, *, yes: bool, approve: str | None) -> Prepared:
    """Turn the two flags into the one binding the library understands.

    ``--approve DIGEST`` is the real thing: it names the payload the analyst read
    on an earlier ``redact`` run, and a deck edited since then no longer answers
    to it. ``--yes`` approves whatever the list says at this moment, which is the
    flag's existing meaning and is only as strong as the habit of having looked —
    it is kept because breaking every pipeline that uses it would buy nothing.
    Both go through :meth:`Prepared.approve`, so ``send`` cannot tell them apart
    and there is no second, looser path into it.
    """
    if approve:
        return prepared.approve(approve.strip())
    if yes:
        return prepared.approve(prepared.digest)
    return prepared


def _build_client(model: str | None) -> ReviewClient:
    """Import the transport only once a send is actually going to happen.

    A local import so that ``tieout-review redact`` works on a machine with no
    ``anthropic`` installed, which is the machine an analyst evaluating the
    redaction would rather be on.
    """
    try:
        from tieout_review.client import (
            DEFAULT_MODEL,
            AnthropicReviewClient,
            ReviewClientError,
        )
    except ImportError as exc:
        # `redact` works without the SDK, and deliberately so: it is the command
        # you run to decide whether to trust any of this. Only `check` needs the
        # transport, and only then is the missing extra worth mentioning.
        # Escaped: rich reads "[review]" as a markup tag and drops it, which
        # turns the fix into "pip install 'tieout'".
        _fail(
            f"sending needs the review extra: {exc.name} is missing. Install it with "
            "pip install 'tieout\\[review]'. The redact command works without it, "
            "and so does tieout itself."
        )
        raise AssertionError("unreachable") from exc  # pragma: no cover

    try:
        return AnthropicReviewClient(model=model or DEFAULT_MODEL)
    except ReviewClientError as exc:
        _fail(str(exc))
        raise AssertionError("unreachable") from exc  # pragma: no cover


def _audit(deck_path: Path, client: str, profile: Path | None) -> AuditResult:
    loaded_profile = load_for_client(client, profile)
    deck = load_deck(deck_path)
    clear_caches()
    return run_rules(
        deck,
        loaded_profile,
        suppressions=load_suppressions(client or loaded_profile.client),
    )


def _emit(
    result: AuditResult,
    deck_path: Path,
    client: str,
    profile: Path | None,
    output_format: str,
    out: Path | None,
    *,
    quiet: bool,
) -> None:
    if output_format == "table":
        if out is not None:
            _fail("--out applies to --format json and html; table prints to stdout")
        console_report.render(result, quiet=quiet)
        return

    if output_format == "json":
        text = json_out.render(result)
    else:
        text = html_report.render(result, load_deck(deck_path))

    if out is None:
        sys.stdout.write(text)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if not quiet:
        _out.print(f"[bold green]Wrote[/bold green] {out}")
        console_report.render(result, quiet=True)


def _usage(usage: dict[str, int]) -> str:
    if not usage or not any(usage.values()):
        return ""
    return (
        f", {usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out tokens"
    )


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
