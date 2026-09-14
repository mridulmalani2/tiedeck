"""The command line interface.

Two verbs carry the product. ``learn`` derives a profile from a deck the client
has already approved; ``check`` audits every deck after that. Everything else is
support: inspecting what was learned, locking a value you have corrected by
hand, and generating a demo deck so a new user can watch the tool work before
trusting it with real material.

``check`` exits 1 when anything at or above ``--fail-on`` is found, so it drops
straight into a pre-send gate or a CI step without a wrapper script.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Final

import typer
from rich.console import Console

from tieout.learn import learn as learn_profile
from tieout.learn.emit import iter_provenance
from tieout.learn.emit import write as write_profile
from tieout.learn.interview import Prompter
from tieout.model.loader import DeckLoadError, load_deck
from tieout.profile.loader import (
    ProfileError,
    load_for_client,
    load_suppressions,
    profile_path,
    suppression_path,
    write_suppressions,
)
from tieout.profile.schema import DeferredQuestion, Suppression
from tieout.report import console as console_report
from tieout.report import html as html_report
from tieout.report import json_out
from tieout.rules.base import clear_caches, run_rules

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Deterministic, offline PowerPoint QA with zero-config client onboarding.",
)
profile_app = typer.Typer(no_args_is_help=True, help="Inspect and edit a learned profile.")
app.add_typer(profile_app, name="profile")

_err = Console(stderr=True)
_out = Console()

#: Exit code when findings at or above the threshold are present.
EXIT_FINDINGS: Final[int] = 1
#: Exit code for a usage or input error, distinct from "the deck has findings"
#: so a pre-send gate can tell a failed check from a failed run.
EXIT_ERROR: Final[int] = 2


class _ConsolePrompter(Prompter):
    """Asks on the terminal. Pressing enter accepts the default."""

    def ask(self, question: DeferredQuestion) -> str:
        _out.print()
        _out.print(f"[bold]{question.question}[/bold]")
        for index, option in enumerate(question.options, start=1):
            marker = " [dim](default)[/dim]" if option == question.default else ""
            _out.print(f"  {index}. {option}{marker}")
        raw = str(
            typer.prompt(
                "  answer (number, text, or enter for the default)",
                default="",
                show_default=False,
            )
        ).strip()
        if not raw:
            return question.default
        if raw.isdigit() and 1 <= int(raw) <= len(question.options):
            return question.options[int(raw) - 1]
        return raw


def _fail(message: str) -> None:
    _err.print(f"[bold red]error[/bold red] {message}")
    raise typer.Exit(EXIT_ERROR)


# --------------------------------------------------------------------------------------
# learn
# --------------------------------------------------------------------------------------


@app.command()
def learn(
    references: Annotated[
        list[Path] | None,
        typer.Argument(help="Reference deck(s) the client has already approved."),
    ] = None,
    client: Annotated[str, typer.Option("--client", help="Client name.")] = "",
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive/--no-interactive",
            help="Ask the questions the evidence could not settle.",
        ),
    ] = False,
    add: Annotated[
        Path | None,
        typer.Option("--add", help="Merge a further deck into an existing profile."),
    ] = None,
    review: Annotated[
        bool,
        typer.Option("--review", help="Answer the questions a previous run deferred."),
    ] = False,
    out: Annotated[
        Path | None, typer.Option("--out", help="Where to write the profile.")
    ] = None,
) -> None:
    """Derive a brand and quality profile from one or more reference decks."""
    if not client:
        _fail("--client NAME is required")

    if review:
        _review(client, out)
        return
    if add is not None:
        _add(client, add, out, interactive=interactive)
        return
    if not references:
        _fail("give at least one reference deck, or use --add or --review")
        return

    prompter = _ConsolePrompter() if interactive else None
    try:
        result = learn_profile([*references], client, prompter=prompter)
    except (DeckLoadError, ValueError) as exc:
        _fail(str(exc))
        return

    target = out or profile_path(client)
    write_profile(result.profile, target, questions_deferred=len(result.interview.deferred))
    _report_learned(result, target)


def _report_learned(result: object, target: Path) -> None:
    from tieout.learn import LearnResult

    assert isinstance(result, LearnResult)
    _out.print()
    _out.print(f"[bold green]Learned[/bold green] {target}")
    _out.print(f"  {result.summary}", markup=False, highlight=False)
    _out.print(result.describe_not_learned(), markup=False, highlight=False)
    deferred = result.interview.deferred
    if deferred:
        _out.print()
        _out.print(
            f"[yellow]{len(deferred)} question(s) were deferred[/yellow] and the "
            f"default applied. They appear as QUESTION comments in the profile; "
            f"answer them with:"
        )
        _out.print(f"  tieout learn --review --client {result.profile.client}")


def _add(client: str, deck_path: Path, out: Path | None, *, interactive: bool) -> None:
    from tieout.learn.merge import merge

    try:
        existing = load_for_client(client)
        addition = learn_profile(
            [deck_path], client, prompter=_ConsolePrompter() if interactive else None
        )
    except (ProfileError, DeckLoadError) as exc:
        _fail(str(exc))
        return

    merged = merge(existing, addition.profile)
    target = out or profile_path(client)
    write_profile(merged.profile, target)

    _out.print()
    _out.print(f"[bold green]Merged[/bold green] {deck_path.name} into {target}")
    _out.print(merged.report(), markup=False, highlight=False)
    if merged.locked_out:
        _out.print()
        _out.print(
            f"[dim]{len(merged.locked_out)} locked field(s) were left "
            f"untouched.[/dim]"
        )


def _review(client: str, out: Path | None) -> None:
    """Answer the questions a non-interactive run deferred."""
    try:
        profile = load_for_client(client)
    except ProfileError as exc:
        _fail(str(exc))
        return

    outstanding = [q for q in profile.questions if not q.answered]
    if not outstanding:
        _out.print("[green]Nothing to review: no questions are outstanding.[/green]")
        return

    prompter = _ConsolePrompter()
    for question in outstanding:
        answer = prompter.ask(question)
        question.answer = answer
        question.answered = True
        # Section 8.6: answering explicitly locks the field, so a later --add
        # cannot quietly overwrite a decision a person made.
        if question.field_path not in profile.locks:
            profile.locks.append(question.field_path)
        profile.set_provenance(
            question.field_path,
            f"answered in review: {answer}",
            "high",
        )

    target = out or profile_path(client)
    write_profile(profile, target)
    _out.print()
    _out.print(
        f"[bold green]Answered[/bold green] {len(outstanding)} question(s) and "
        f"locked their fields in {target}"
    )


# --------------------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------------------


@app.command()
def check(
    deck_path: Annotated[Path, typer.Argument(help="The deck to audit.")],
    client: Annotated[str, typer.Option("--client", help="Client name.")] = "",
    profile: Annotated[
        Path | None, typer.Option("--profile", help="Use this profile instead.")
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="table, json or html.")
    ] = "table",
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the report here.")
    ] = None,
    severity: Annotated[
        str | None,
        typer.Option("--severity", help="Report only this severity or worse."),
    ] = None,
    rules: Annotated[
        str | None,
        typer.Option("--rules", help="Comma-separated rule ids or globs, e.g. BR-*."),
    ] = None,
    exclude: Annotated[
        str | None, typer.Option("--exclude", help="Comma-separated rule ids to skip.")
    ] = None,
    accept: Annotated[
        list[str] | None,
        typer.Option("--accept", help="Accept a finding, as RULE@slideN."),
    ] = None,
    fail_on: Annotated[
        str, typer.Option("--fail-on", help="Exit 1 at this severity or worse.")
    ] = "blocker",
    quiet: Annotated[bool, typer.Option("--quiet", help="Print only the summary.")] = False,
) -> None:
    """Audit a deck against a learned profile."""
    if not client and profile is None:
        _fail("--client NAME or --profile PATH is required")
    if output_format not in ("table", "json", "html"):
        _fail(f"unknown --format {output_format!r}: use table, json or html")

    try:
        loaded_profile = load_for_client(client, profile)
        deck = load_deck(deck_path)
    except (ProfileError, DeckLoadError) as exc:
        _fail(str(exc))
        return

    if accept:
        _accept(client or loaded_profile.client, accept)

    suppressions = load_suppressions(client or loaded_profile.client)
    clear_caches()
    result = run_rules(
        deck,
        loaded_profile,
        include=_split(rules),
        exclude=_split(exclude),
        suppressions=suppressions,
        min_severity=severity,
    )

    _emit(result, deck, output_format, out, quiet=quiet)
    _suggest_relearn(suppressions, client or loaded_profile.client)

    if result.exceeds(fail_on):
        raise typer.Exit(EXIT_FINDINGS)


def _emit(
    result: object,
    deck: object,
    output_format: str,
    out: Path | None,
    *,
    quiet: bool,
) -> None:
    from tieout.model.deck import DeckModel
    from tieout.rules.base import AuditResult

    assert isinstance(result, AuditResult)
    assert isinstance(deck, DeckModel)

    if output_format == "json":
        text = json_out.render(result)
    elif output_format == "html":
        text = html_report.render(result, deck)
    else:
        if out is not None:
            _fail("--out applies to --format json and html; table prints to stdout")
        console_report.render(result, quiet=quiet)
        return

    if out is None:
        sys.stdout.write(text)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if not quiet:
        _out.print(f"[bold green]Wrote[/bold green] {out}")
        console_report.render(result, quiet=True)


def _accept(client: str, entries: list[str]) -> None:
    """Append accepted findings, keeping a count of repeat acceptances."""
    suppressions = load_suppressions(client)
    for entry in entries:
        rule_id, _, slide = entry.partition("@")
        rule_id = rule_id.strip()
        if not rule_id:
            _fail(f"cannot read --accept {entry!r}: expected RULE@slideN")
        slide_index: int | None = None
        if slide:
            digits = slide.strip().lower().removeprefix("slide")
            if not digits.isdigit():
                _fail(f"cannot read --accept {entry!r}: expected RULE@slideN")
            slide_index = int(digits)

        existing = next(
            (
                s
                for s in suppressions.suppressions
                if s.rule_id == rule_id and s.slide_index == slide_index
            ),
            None,
        )
        if existing is None:
            suppressions.suppressions.append(
                Suppression(rule_id=rule_id, slide_index=slide_index, count=1)
            )
        else:
            existing.count += 1
    suppressions.client = client
    write_suppressions(suppressions, suppression_path(client))


def _suggest_relearn(suppressions: object, client: str) -> None:
    """Section 8.7: three acceptances means the rule, not the deck, is wrong."""
    from tieout.profile.schema import SuppressionFile

    assert isinstance(suppressions, SuppressionFile)
    repeats = suppressions.repeat_offenders()
    if not repeats:
        return
    _out.print()
    for entry in repeats:
        _out.print(
            f"[yellow]{entry.rule_id} has been accepted {entry.count} times."
            f"[/yellow] It is probably miscalibrated. Fold the evidence in with:"
        )
        _out.print(f"  tieout learn --add THAT_DECK.pptx --client {client}")


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


# --------------------------------------------------------------------------------------
# rules, profile, scaffold
# --------------------------------------------------------------------------------------


@app.command()
def rules(
    client: Annotated[
        str, typer.Option("--client", help="Show this client's enabled state.")
    ] = "",
) -> None:
    """List every rule, its severity, whether it runs, and what it reads."""
    console_report.render_rules_table(client=client)
    if not client:
        return
    try:
        profile = load_for_client(client)
    except ProfileError as exc:
        _out.print(f"[dim]{exc}[/dim]")
        return
    if profile.rules.disabled:
        _out.print(f"disabled for {client}: {', '.join(profile.rules.disabled)}")
    if profile.rules.enabled:
        _out.print(f"opted in for {client}: {', '.join(profile.rules.enabled)}")
    if profile.not_learned:
        _out.print()
        _out.print(f"[bold]not learned for {client}[/bold], so not checked:")
        for entry in profile.not_learned:
            _out.print(f"  [dim]{entry.key}[/dim]  {entry.reason}")


@profile_app.command("show")
def profile_show(
    client: Annotated[str, typer.Option("--client", help="Client name.")] = "",
) -> None:
    """Print the profile with the evidence behind every derived value."""
    if not client:
        _fail("--client NAME is required")
    try:
        profile = load_for_client(client)
    except ProfileError as exc:
        _fail(str(exc))
        return

    _out.print(f"[bold]{client}[/bold]  profile v{profile.version}")
    _out.print(f"learned from: {', '.join(profile.sources) or 'unrecorded'}")
    _out.print()
    for path, note, confidence in iter_provenance(profile):
        marker = "" if confidence == "high" else f" [dim]({confidence})[/dim]"
        _out.print(f"[bold]{path}[/bold]{marker}")
        _out.print(f"  [dim]{note}[/dim]")
    if profile.locks:
        _out.print()
        _out.print(f"[bold]locked[/bold]: {', '.join(profile.locks)}")


@profile_app.command("lock")
def profile_lock(
    client: Annotated[str, typer.Option("--client", help="Client name.")] = "",
    field: Annotated[
        str, typer.Option("--field", help="Dotted field path to protect.")
    ] = "",
) -> None:
    """Protect a hand-edited value from being overwritten by re-learning."""
    if not client or not field:
        _fail("--client NAME and --field PATH are both required")
    try:
        profile = load_for_client(client)
    except ProfileError as exc:
        _fail(str(exc))
        return

    if field in profile.locks:
        _out.print(f"[dim]{field} is already locked.[/dim]")
        return
    profile.locks.append(field)
    write_profile(profile, profile_path(client))
    _out.print(f"[bold green]Locked[/bold green] {field} in {profile_path(client)}")


@app.command("scaffold-reference")
def scaffold_reference(
    out: Annotated[Path, typer.Option("--out", help="Directory to write into.")],
    spec: Annotated[
        Path | None,
        typer.Option("--spec", help="Write the specification here as well."),
    ] = None,
    dirty: Annotated[
        bool, typer.Option("--dirty/--clean-only", help="Also build the seeded deck.")
    ] = True,
) -> None:
    """Generate a synthetic investment-banking style reference deck.

    Everything in it is invented. It exists so a new user can run learn and check
    end to end, on a deck whose every parameter is known, before pointing the
    tool at real client material.
    """
    from tieout.fixtures.generator import build_all, build_clean

    out.mkdir(parents=True, exist_ok=True)
    if dirty:
        built = build_all(out)
        _out.print(f"[bold green]Built[/bold green] {built.clean.name} (clean)")
        _out.print(f"       {built.dirty.name} (one seeded defect per rule)")
        for name, path in sorted(built.variants.items()):
            _out.print(f"       {path.name} ({name.replace('_', ' ')})")
        _out.print(f"       {built.spec_path.name} (the parameters it was built from)")
        if spec is not None:
            spec.parent.mkdir(parents=True, exist_ok=True)
            spec.write_text(built.spec_path.read_text(encoding="utf-8"), encoding="utf-8")
            _out.print(f"[bold green]Wrote[/bold green] {spec}")
    else:
        path = build_clean(out / "reference_clean.pptx")
        _out.print(f"[bold green]Built[/bold green] {path}")

    _out.print()
    _out.print("Try it:")
    _out.print(f"  tieout learn {out / 'reference_clean.pptx'} --client demo")
    _out.print(f"  tieout check {out / 'reference_clean.pptx'} --client demo")
    if dirty:
        _out.print(f"  tieout check {out / 'reference_dirty.pptx'} --client demo")


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
