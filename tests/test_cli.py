"""The command line interface.

Exercised through typer's runner rather than a subprocess so failures surface as
tracebacks rather than exit codes, but otherwise exactly as a user drives it.

The exit codes carry real weight: ``check`` is specified to work as a pre-send
gate, which means 0 for a deck that passes, 1 for a deck with findings, and
something else again for a run that failed -- a gate that cannot tell "this deck
is bad" from "the tool broke" will eventually wave a bad deck through.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tieout.cli import EXIT_ERROR, EXIT_FINDINGS, app
from tieout.profile.loader import PROFILE_DIR_ENV

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path, clean_path, dirty_path, monkeypatch):
    """An isolated working directory with the fixture decks and a profiles dir."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "profiles"))
    (tmp_path / "decks").mkdir()
    for source in (clean_path, dirty_path):
        (tmp_path / "decks" / source.name).write_bytes(source.read_bytes())
    return tmp_path


def _invoke(*args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def _learn(workspace: Path) -> None:
    result = _invoke("learn", "decks/reference_clean.pptx", "--client", "demo")
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------------------
# learn
# --------------------------------------------------------------------------------------


def test_learn_writes_a_profile_and_reports_what_it_did(workspace):
    result = _invoke("learn", "decks/reference_clean.pptx", "--client", "demo")
    assert result.exit_code == 0, result.output
    assert (workspace / "profiles" / "demo.yaml").exists()
    assert "Learned" in result.output
    assert "No questions" in result.output


def test_learn_reports_what_it_declined_to_derive(workspace):
    """Silence about an unlearned rule would let a user assume the check exists."""
    result = _invoke("learn", "decks/reference_clean.pptx", "--client", "demo")
    assert "not derived" in result.output
    assert "chart_label" in result.output


def test_learn_honours_an_explicit_output_path(workspace):
    result = _invoke(
        "learn", "decks/reference_clean.pptx", "--client", "demo", "--out", "custom.yaml"
    )
    assert result.exit_code == 0
    assert (workspace / "custom.yaml").exists()


def test_learn_without_a_client_is_a_usage_error(workspace):
    result = _invoke("learn", "decks/reference_clean.pptx")
    assert result.exit_code == EXIT_ERROR
    assert "--client" in result.output


def test_learn_with_no_deck_at_all_is_a_usage_error(workspace):
    result = _invoke("learn", "--client", "demo")
    assert result.exit_code == EXIT_ERROR


def test_learn_on_a_missing_file_fails_cleanly(workspace):
    result = _invoke("learn", "decks/nope.pptx", "--client", "demo")
    assert result.exit_code == EXIT_ERROR
    assert "no such file" in result.output


def test_learn_add_merges_and_prints_the_diff(workspace):
    _learn(workspace)
    result = _invoke("learn", "--add", "decks/reference_dirty.pptx", "--client", "demo")
    assert result.exit_code == 0, result.output
    assert "Merged" in result.output
    assert "change(s) from the added deck" in result.output


def test_learn_add_without_an_existing_profile_fails_cleanly(workspace):
    result = _invoke("learn", "--add", "decks/reference_dirty.pptx", "--client", "ghost")
    assert result.exit_code == EXIT_ERROR
    assert "no profile" in result.output


def test_learn_review_says_so_when_there_is_nothing_to_review(workspace):
    _learn(workspace)
    result = _invoke("learn", "--review", "--client", "demo")
    assert result.exit_code == 0
    assert "Nothing to review" in result.output


# --------------------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------------------


def test_check_passes_a_clean_deck_with_exit_zero(workspace):
    _learn(workspace)
    result = _invoke("check", "decks/reference_clean.pptx", "--client", "demo")
    assert result.exit_code == 0, result.output
    assert "No findings" in result.output


def test_check_fails_a_dirty_deck_with_exit_one(workspace):
    _learn(workspace)
    result = _invoke("check", "decks/reference_dirty.pptx", "--client", "demo")
    assert result.exit_code == EXIT_FINDINGS
    assert "finding(s)" in result.output


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [("blocker", 0), ("major", 0), ("minor", EXIT_FINDINGS)],
)
def test_fail_on_decides_the_exit_code(workspace, threshold, expected):
    """A minor-only finding should gate a release only if the firm says so."""
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--rules",
        "TY-002",
        "--fail-on",
        threshold,
        "--quiet",
    )
    assert result.exit_code == expected, result.output


def test_check_writes_json_with_a_stable_key_set(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--format",
        "json",
        "--out",
        "report.json",
        "--quiet",
    )
    assert result.exit_code == EXIT_FINDINGS
    payload = json.loads((workspace / "report.json").read_text())
    assert payload["schema_version"] == 1
    assert payload["findings"]
    assert "unchecked" in payload and "rules_skipped" in payload


def test_check_writes_a_self_contained_html_report(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--format",
        "html",
        "--out",
        "report.html",
        "--quiet",
    )
    assert result.exit_code == EXIT_FINDINGS
    markup = (workspace / "report.html").read_text()
    for forbidden in ("http://", "https://", "fonts.googleapis"):
        assert forbidden not in markup


def test_json_goes_to_stdout_without_an_out_path(workspace):
    _learn(workspace)
    result = _invoke(
        "check", "decks/reference_clean.pptx", "--client", "demo", "--format", "json"
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["summary"]["total"] == 0


def test_rules_and_exclude_narrow_the_run(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--rules",
        "HY-*",
        "--exclude",
        "HY-001",
        "--format",
        "json",
    )
    payload = json.loads(result.stdout)
    assert payload["findings"]
    assert all(f["rule_id"].startswith("HY-") for f in payload["findings"])
    assert all(f["rule_id"] != "HY-001" for f in payload["findings"])


def test_severity_filters_the_report(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--severity",
        "blocker",
        "--format",
        "json",
    )
    payload = json.loads(result.stdout)
    assert payload["findings"]
    assert {f["severity"] for f in payload["findings"]} == {"blocker"}


def test_an_unknown_format_is_a_usage_error(workspace):
    _learn(workspace)
    result = _invoke(
        "check", "decks/reference_clean.pptx", "--client", "demo", "--format", "xml"
    )
    assert result.exit_code == EXIT_ERROR
    assert "unknown --format" in result.output


def test_out_with_the_table_format_is_a_usage_error(workspace):
    """Rich markup in a file is not a report anyone wants; better to say so than
    to write it."""
    _learn(workspace)
    result = _invoke(
        "check", "decks/reference_clean.pptx", "--client", "demo", "--out", "x.txt"
    )
    assert result.exit_code == EXIT_ERROR


def test_checking_against_a_missing_profile_fails_cleanly(workspace):
    result = _invoke("check", "decks/reference_clean.pptx", "--client", "ghost")
    assert result.exit_code == EXIT_ERROR
    assert "tieout learn" in result.output, "the error should say how to fix itself"


def test_an_explicit_profile_path_needs_no_client(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--profile",
        str(workspace / "profiles" / "demo.yaml"),
        "--quiet",
    )
    assert result.exit_code == 0


def test_check_without_a_client_or_a_profile_is_a_usage_error(workspace):
    result = _invoke("check", "decks/reference_clean.pptx")
    assert result.exit_code == EXIT_ERROR


def test_accept_suppresses_a_finding_and_records_it(workspace):
    _learn(workspace)
    before = _invoke(
        "check", "decks/reference_dirty.pptx", "--client", "demo", "--rules", "BR-002"
    )
    assert before.exit_code in (0, EXIT_FINDINGS)
    assert "BR-002" in before.output

    after = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--rules",
        "BR-002",
        "--accept",
        "BR-002@slide5",
    )
    assert "No findings" in after.output
    assert (workspace / "profiles" / "demo.suppress.yaml").exists()


def test_three_acceptances_suggest_relearning_instead(workspace):
    """Section 8.7: a rule accepted three times is miscalibrated, and the fix is
    to give the learner the evidence rather than to keep suppressing it."""
    _learn(workspace)
    for _ in range(3):
        result = _invoke(
            "check",
            "decks/reference_dirty.pptx",
            "--client",
            "demo",
            "--rules",
            "BR-002",
            "--accept",
            "BR-002@slide5",
            "--quiet",
        )
    assert "miscalibrated" in result.output
    assert "learn --add" in result.output


def test_an_acceptance_can_record_why(workspace):
    """``Suppression.note`` existed in the schema with no CLI path able to write
    it, so the field was dead. It is the only thing that makes the count in
    ``_suggest_relearn`` actionable: three acceptances says the rule is
    miscalibrated, and whoever reads that is rarely whoever accepted them."""
    _learn(workspace)
    _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--rules",
        "BR-002",
        "--accept",
        "BR-002@slide5",
        "--accept-note",
        "the sponsor logo is contractually this colour",
        "--quiet",
    )

    written = (workspace / "profiles" / "demo.suppress.yaml").read_text(encoding="utf-8")
    assert "contractually this colour" in written


def test_repeat_acceptances_accumulate_their_reasons(workspace):
    """The second reason for accepting a finding is evidence, not a correction of
    the first, so notes are appended rather than overwritten -- and a reason
    already recorded is not recorded twice."""
    _learn(workspace)
    for note in ("first reason", "first reason", "second reason"):
        result = _invoke(
            "check",
            "decks/reference_dirty.pptx",
            "--client",
            "demo",
            "--rules",
            "BR-002",
            "--accept",
            "BR-002@slide5",
            "--accept-note",
            note,
            "--quiet",
        )

    written = (workspace / "profiles" / "demo.suppress.yaml").read_text(encoding="utf-8")
    assert "first reason; second reason" in written
    assert "miscalibrated" in result.output
    assert "first reason; second reason" in result.output


def test_an_unexplained_repeat_acceptance_says_what_is_missing(workspace):
    """A bare count of three tells the next person nothing about what to fold in.
    Said once, where they can act on it."""
    _learn(workspace)
    for _ in range(3):
        result = _invoke(
            "check",
            "decks/reference_dirty.pptx",
            "--client",
            "demo",
            "--rules",
            "BR-002",
            "--accept",
            "BR-002@slide5",
            "--quiet",
        )
    assert "--accept-note" in result.output


def test_an_accept_note_with_nothing_to_annotate_is_a_usage_error(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--accept-note",
        "why",
    )
    assert result.exit_code == EXIT_ERROR


def test_a_malformed_accept_is_a_usage_error(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--accept",
        "BR-002@page five",
    )
    assert result.exit_code == EXIT_ERROR


def test_accept_without_a_slide_suppresses_the_rule_everywhere(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--rules",
        "LO-004",
        "--accept",
        "LO-004",
        "--quiet",
    )
    assert "0 findings" in result.output


# --------------------------------------------------------------------------------------
# rules, profile, scaffold
# --------------------------------------------------------------------------------------


def test_rules_lists_the_whole_catalogue():
    result = _invoke("rules")
    assert result.exit_code == 0
    for rule_id in ("BR-001", "LO-008", "TY-009", "HY-007"):
        assert rule_id in result.output


def test_rules_for_a_client_shows_what_is_off_and_why(workspace):
    _learn(workspace)
    result = _invoke("rules", "--client", "demo")
    assert result.exit_code == 0
    assert "TY-009" in result.output
    assert "not learned" in result.output


def test_rules_for_an_unknown_client_still_lists_the_catalogue():
    result = _invoke("rules", "--client", "ghost")
    assert result.exit_code == 0
    assert "BR-001" in result.output


def test_profile_show_prints_the_evidence_behind_each_value(workspace):
    _learn(workspace)
    result = _invoke("profile", "show", "--client", "demo")
    assert result.exit_code == 0
    assert "brand.palette_hex" in result.output
    assert "weighted colour clusters" in result.output


def test_profile_show_needs_a_client():
    assert _invoke("profile", "show").exit_code == EXIT_ERROR


def test_profile_lock_protects_a_field(workspace):
    _learn(workspace)
    result = _invoke(
        "profile", "lock", "--client", "demo", "--field", "brand.palette_hex"
    )
    assert result.exit_code == 0
    assert "Locked" in result.output

    from tieout.profile.loader import load

    assert "brand.palette_hex" in load(workspace / "profiles" / "demo.yaml").locks


def test_locking_twice_is_not_an_error(workspace):
    _learn(workspace)
    _invoke("profile", "lock", "--client", "demo", "--field", "brand.palette_hex")
    again = _invoke(
        "profile", "lock", "--client", "demo", "--field", "brand.palette_hex"
    )
    assert again.exit_code == 0
    assert "already locked" in again.output


def test_profile_lock_needs_both_arguments(workspace):
    assert _invoke("profile", "lock", "--client", "demo").exit_code == EXIT_ERROR


def test_a_locked_value_survives_a_merge(workspace):
    """The whole point of a lock, exercised through the CLI as a user would."""
    _learn(workspace)
    _invoke(
        "profile",
        "lock",
        "--client",
        "demo",
        "--field",
        "brand.fonts.allowed",
    )
    from tieout.profile.loader import load

    before = load(workspace / "profiles" / "demo.yaml").brand.fonts.allowed
    result = _invoke("learn", "--add", "decks/reference_dirty.pptx", "--client", "demo")
    assert result.exit_code == 0
    after = load(workspace / "profiles" / "demo.yaml").brand.fonts.allowed
    assert after == before
    assert "locked" in result.output


@pytest.mark.slow
def test_scaffold_reference_builds_a_runnable_demo(workspace):
    result = _invoke("scaffold-reference", "--out", "demo")
    assert result.exit_code == 0, result.output
    assert (workspace / "demo" / "reference_clean.pptx").exists()
    assert (workspace / "demo" / "reference_dirty.pptx").exists()
    assert (workspace / "demo" / "reference_spec.yaml").exists()
    assert "tieout learn" in result.output, "it should tell the user what to run next"


@pytest.mark.slow
def test_scaffold_can_build_only_the_clean_deck(workspace):
    result = _invoke("scaffold-reference", "--out", "demo", "--clean-only")
    assert result.exit_code == 0
    assert (workspace / "demo" / "reference_clean.pptx").exists()
    assert not (workspace / "demo" / "reference_dirty.pptx").exists()


def test_the_profile_directory_honours_its_environment_variable(
    tmp_path, clean_path, monkeypatch
):
    """A firm that keeps profiles on a shared drive should not have to pass
    --profile on every invocation."""
    monkeypatch.chdir(tmp_path)
    elsewhere = tmp_path / "shared"
    monkeypatch.setenv(PROFILE_DIR_ENV, str(elsewhere))
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(clean_path.read_bytes())

    assert _invoke("learn", str(deck), "--client", "shared").exit_code == 0
    assert (elsewhere / "shared.yaml").exists()
    assert not (tmp_path / "profiles").exists()


# --------------------------------------------------------------------------------------
# An audit that did not cover the deck must not read as a clean one
# --------------------------------------------------------------------------------------


def test_a_crashed_rule_fails_the_run(workspace, monkeypatch):
    """The console said "1 rule failed to run" in red and the exit code said
    zero. A pre-send gate reads the exit code, so a deck could ship because the
    checker broke rather than because it was clean."""
    from tieout.rules import layout

    def explode(self, deck, profile):
        raise RuntimeError("malformed chart part")

    monkeypatch.setattr(layout.NearMissAlignment, "run", explode)
    _learn(workspace)
    result = _invoke(
        "check", "decks/reference_clean.pptx", "--client", "demo", "--rules", "LO-003"
    )

    assert result.exit_code == EXIT_ERROR
    assert "Incomplete audit" in result.output
    assert "malformed chart part" in result.output


# --------------------------------------------------------------------------------------
# An acceptance covers what it was given, not the whole slide
# --------------------------------------------------------------------------------------


def test_an_acceptance_can_be_scoped_to_one_shape(workspace):
    """Accepting a judged-correct finding used to blind its rule for the whole
    slide, so a genuine defect introduced on the next turn of the deck was filed
    as already accepted and never shown."""
    _learn(workspace)
    _invoke(
        "check",
        "decks/reference_dirty.pptx",
        "--client",
        "demo",
        "--rules",
        "BR-002",
        "--accept",
        "BR-002@slide5:Some Other Shape",
        "--quiet",
    )
    written = (workspace / "profiles" / "demo.suppress.yaml").read_text(encoding="utf-8")
    assert "Some Other Shape" in written

    # The finding is on a different shape, so the acceptance must not hide it.
    after = _invoke(
        "check", "decks/reference_dirty.pptx", "--client", "demo", "--rules", "BR-002"
    )
    assert "BR-002" in after.output, (
        "an acceptance scoped to one shape must not suppress another shape's finding"
    )


def test_a_shape_scoped_acceptance_needs_a_slide(workspace):
    _learn(workspace)
    result = _invoke(
        "check",
        "decks/reference_clean.pptx",
        "--client",
        "demo",
        "--accept",
        "BR-002@:Ring 3",
    )
    assert result.exit_code == EXIT_ERROR



def test_an_unknown_gate_confidence_is_a_usage_error(workspace):
    _learn(workspace)
    result = _invoke(
        "check", "decks/reference_clean.pptx", "--client", "demo",
        "--gate-confidence", "certain",
    )
    assert result.exit_code == EXIT_ERROR


def test_the_gate_confidence_is_accepted(workspace):
    _learn(workspace)
    result = _invoke(
        "check", "decks/reference_clean.pptx", "--client", "demo",
        "--gate-confidence", "low", "--quiet",
    )
    assert result.exit_code in (0, EXIT_FINDINGS)
