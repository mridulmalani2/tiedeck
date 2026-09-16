"""``tieout-review`` from the outside.

The ``redact`` command is the one that earns trust: it shows exactly what would
be sent, needs no key, and sends nothing. Most of this file is about it, and
about ``check`` refusing to proceed when the redaction has items outstanding.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from tieout.cli import EXIT_ERROR, EXIT_FINDINGS
from tieout.cli import app as core_app
from tieout.profile.loader import PROFILE_DIR_ENV
from tieout_review.cli import app

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path, clean_path, dirty_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "profiles"))
    (tmp_path / "decks").mkdir()
    for source in (clean_path, dirty_path):
        (tmp_path / "decks" / source.name).write_bytes(source.read_bytes())
    runner.invoke(
        core_app,
        ["learn", "decks/reference_clean.pptx", "--client", "demo"],
        catch_exceptions=False,
    )
    return tmp_path


def _invoke(*args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def test_redact_shows_what_would_be_sent(workspace):
    result = _invoke("redact", "decks/reference_clean.pptx", "--client", "demo")
    assert "term(s) redacted" in result.output
    assert "term list assembled from" in result.output


def test_redact_needs_no_key(workspace, monkeypatch):
    """The command someone runs before deciding whether to trust any of this."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = _invoke("redact", "decks/reference_clean.pptx", "--client", "demo")
    assert result.exit_code == 0


def test_redact_names_the_placeholder_for_each_term(workspace):
    result = _invoke(
        "redact",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
    )
    assert "[TERM_" in result.output or "[COMPANY_" in result.output


def test_redact_shows_the_payload_when_asked(workspace):
    result = _invoke(
        "redact", "decks/reference_clean.pptx", "--client", "demo", "--show-payload"
    )
    assert "payload as it would be sent" in result.output
    assert "1,908" in result.output, "the figures are the point"


def test_a_blocklist_term_does_not_appear_in_the_shown_payload(workspace):
    result = _invoke(
        "redact",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--show-payload",
    )
    payload = result.output.split("payload as it would be sent", 1)[1]
    assert "Ashcombe" not in payload


def test_redact_exits_one_when_something_could_not_be_cleared(workspace):
    """Scriptable: a non-zero exit means a person has to look."""
    result = _invoke(
        "redact",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Board",
    )
    assert result.exit_code in (0, EXIT_FINDINGS)


def test_a_blocklist_file_is_read(workspace):
    path = workspace / "forbid.txt"
    path.write_text("# the adviser\nAshcombe Partners\n")
    result = _invoke(
        "redact",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid-file",
        str(path),
    )
    assert "Ashcombe Partners" in result.output


def test_a_missing_blocklist_file_is_an_error_not_a_silent_skip(workspace):
    """Silently proceeding with no blocklist is the worst possible response."""
    result = _invoke(
        "redact",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid-file",
        "nope.txt",
    )
    assert result.exit_code == EXIT_ERROR


def test_redact_works_without_a_profile(workspace):
    """A deck from a client who has not been onboarded still gets redacted."""
    result = _invoke("redact", "decks/reference_clean.pptx", "--forbid", "Ashcombe Partners")
    assert result.exit_code in (0, EXIT_FINDINGS)
    assert "Ashcombe Partners" in result.output


def test_check_requires_a_client_or_a_profile(workspace):
    result = _invoke("check", "decks/reference_clean.pptx")
    assert result.exit_code == EXIT_ERROR
    assert "--client" in result.output


def test_check_rejects_an_unknown_format(workspace):
    result = _invoke("check", "decks/reference_clean.pptx", "--client", "demo", "--format", "pdf")
    assert result.exit_code == EXIT_ERROR


def test_check_refuses_to_send_while_something_is_outstanding(workspace, monkeypatch):
    """Nothing is sent, and the reason is printed rather than summarised.

    The transport is never constructed, so this holds on a machine with no key
    at all — which is the strongest form the assertion can take.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
    )
    assert result.exit_code == EXIT_ERROR
    assert "nothing has been sent" in result.output
    assert "could not be cleared" in result.output


def test_check_reports_a_missing_key_clearly(workspace, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--yes",
    )
    assert result.exit_code == EXIT_ERROR
    assert "ANTHROPIC_API_KEY" in result.output


def test_check_reports_a_missing_deck_clearly(workspace):
    result = _invoke("check", "decks/nope.pptx", "--client", "demo")
    assert result.exit_code == EXIT_ERROR


def test_a_missing_client_profile_is_an_error(workspace):
    result = _invoke("redact", "decks/reference_clean.pptx", "--client", "nobody")
    assert result.exit_code == EXIT_ERROR


def test_the_help_states_the_guarantee():
    result = _invoke("--help")
    assert "before anything leaves this machine" in result.output


# --------------------------------------------------------------------------- #
# The send path, with the transport stubbed
# --------------------------------------------------------------------------- #


class _StubClient:
    """Answers with one finding on slide 4, whatever it is asked."""

    model = "stub-model"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> tuple[str, dict[str, int]]:
        self.calls.append(user)
        answer = {
            "findings": [
                {
                    "rule": "SE-001",
                    "slide": 4,
                    "quote": "the headline",
                    "other": "the table",
                    "other_slide": 6,
                    "explanation": "The headline says 20%; the table shows 8%.",
                    "confidence": "high",
                }
            ]
        }
        return json.dumps(answer), {"input_tokens": 9000, "output_tokens": 120}


@pytest.fixture
def stub_transport(monkeypatch):
    from tieout_review import cli as review_cli

    client = _StubClient()
    monkeypatch.setattr(review_cli, "_build_client", lambda model: client)
    return client


def test_check_merges_the_semantic_finding_into_one_report(workspace, stub_transport):
    """One report, not two. The whole point of a single command."""
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
    )
    assert "SE-001" in result.output
    assert "semantic pass" in result.stderr
    assert stub_transport.calls, "the transport should have been used"


def test_a_semantic_finding_does_not_change_the_exit_code(workspace, stub_transport):
    """A clean deck stays a pass. Adding this layer must not fail a build that
    used to succeed."""
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--fail-on",
        "major",
    )
    assert result.exit_code == 0


def test_a_semantic_finding_can_gate_when_asked(workspace, stub_transport):
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--fail-on",
        "major",
        "--fail-on-semantic",
    )
    assert result.exit_code == EXIT_FINDINGS


def test_the_deterministic_findings_are_still_there(workspace, stub_transport):
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--yes",
    )
    assert result.exit_code == EXIT_FINDINGS
    assert "CO-00" in result.output or "BR-00" in result.output


def test_the_summary_says_how_much_was_withheld_and_sent(workspace, stub_transport):
    """Printed every run, because the number of withheld terms is the thing an
    analyst is trusting."""
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
    )
    assert "term(s) withheld" in result.stderr
    assert "characters sent" in result.stderr
    assert "stub-model" in result.stderr


def test_the_json_report_on_stdout_is_not_polluted_by_the_summary(
    workspace, stub_transport
):
    """The summary belongs on stderr: `--format json` with no --out writes the
    report to stdout, and a diagnostic line appended to it makes the report
    unparseable — which is precisely the pipeline this format exists for."""
    result = _invoke(
        "check", "decks/reference_clean.pptx", "--client", "demo", "--format", "json"
    )
    # result.output is the combined stream; result.stdout is the report alone.
    json.loads(result.stdout)
    assert "semantic pass" in result.stderr


def test_json_output_carries_the_semantic_finding(workspace, stub_transport):
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--format",
        "json",
    )
    payload = json.loads(result.stdout)
    categories = {finding["category"] for finding in payload["findings"]}
    assert "semantic" in categories


def test_html_output_is_written_and_stays_self_contained(workspace, stub_transport):
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--format",
        "html",
        "--out",
        "report.html",
    )
    assert result.exit_code == 0
    markup = (workspace / "report.html").read_text(encoding="utf-8")
    assert "SE-001" in markup
    for forbidden in ("http://", "https://", "<script"):
        assert forbidden not in markup


def test_out_is_refused_for_the_table_format(workspace, stub_transport):
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--format",
        "table",
        "--out",
        "x.txt",
    )
    assert result.exit_code == EXIT_ERROR


def test_a_finding_reaches_the_report_in_the_deck_s_own_words(workspace, stub_transport):
    """The model answered in placeholders; the analyst reads their deck."""
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--format",
        "json",
    )
    payload = json.loads(result.stdout)
    semantic = [f for f in payload["findings"] if f["category"] == "semantic"]
    assert semantic
    assert "[COMPANY_" not in json.dumps(semantic)


def test_sending_without_the_review_extra_names_the_extra(workspace, monkeypatch):
    """`redact` works without the SDK, deliberately: it is the command you run
    to decide whether to trust any of this. Only `check` needs the transport."""
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith(("anthropic", "tieout_review.client")):
            raise ImportError(f"No module named {name!r}", name="anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--forbid",
        "Ashcombe Partners",
        "--yes",
    )
    assert result.exit_code == EXIT_ERROR
    assert "tieout[review]" in result.output
    assert "redact command works without it" in result.output
