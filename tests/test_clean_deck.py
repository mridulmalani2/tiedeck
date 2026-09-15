"""The false-positive guard.

Section 12 calls this the most important ongoing test, and it is right. A rule
that catches its seeded defect but also fires on a correct slide is worse than no
rule: a report with false positives in it teaches the reader to skim, and once
they skim they miss the real finding too.

So this module runs the whole catalogue against the deck the profile was learned
from and asserts silence, rule by rule so a regression names the culprit. It also
asserts the shape of what the tool says about a clean deck -- the counts, the
accounting of what was not checked, the exit code, and every report format --
because "zero findings" is only trustworthy alongside "and here is everything I
looked at".
"""

from __future__ import annotations

import json

import pytest

from tieout.learn import learn_from_decks
from tieout.report import console as console_report
from tieout.report import html as html_report
from tieout.report import json_out
from tieout.rules.base import CATEGORIES, clear_caches, load_all_rules, run_rules

ALL_RULE_IDS = sorted(load_all_rules())

#: The catalogue as it stands. Stated here so a rule added or removed without
#: updating the README and the round-trip test fails loudly in one place.
EXPECTED_PER_CATEGORY = {
    "brand": 11,
    "layout": 9,
    "typography": 9,
    "hygiene": 9,
    "consistency": 3,
    "chart": 5,
}
EXPECTED_RULE_COUNT = sum(EXPECTED_PER_CATEGORY.values())


@pytest.fixture(scope="module")
def derived_profile(clean_deck):
    """The profile the clean deck produces, with everything switched on.

    Enabling LO-006 and TY-009 makes this a stricter guard than the default: the
    two rules that ship off are exactly the ones most likely to be noisy, so they
    are the ones most worth holding to silence here.
    """
    clear_caches()
    profile = learn_from_decks([clean_deck], "guard").profile
    profile.rules.disabled = []
    profile.rules.enabled = ["LO-006", "TY-009"]
    return profile


@pytest.fixture(scope="module")
def clean_result(clean_deck, derived_profile):
    clear_caches()
    return run_rules(clean_deck, derived_profile)


# --------------------------------------------------------------------------------------
# Silence
# --------------------------------------------------------------------------------------


def test_the_whole_catalogue_is_silent_on_the_clean_deck(clean_result):
    assert clean_result.findings == [], "\n".join(
        f"slide {f.slide_index} {f.rule_id} [{f.severity}] "
        f"{f.shape_name or '-'}: {f.message}"
        for f in clean_result.findings
    )


@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_each_rule_individually_is_silent_on_the_clean_deck(
    rule_id, clean_deck, derived_profile
):
    """Run one rule at a time so a failure names the rule rather than the suite."""
    clear_caches()
    result = run_rules(clean_deck, derived_profile, include=[rule_id])
    findings = [f for f in result.findings if f.rule_id == rule_id]
    assert not findings, "; ".join(
        f"slide {f.slide_index}: {f.message}" for f in findings
    )


def test_every_rule_either_ran_or_said_why_not(clean_result):
    accounted = set(clean_result.rules_run) | {
        s.rule_id for s in clean_result.rules_skipped
    }
    assert accounted == set(ALL_RULE_IDS), (
        f"unaccounted for: {sorted(set(ALL_RULE_IDS) - accounted)}"
    )


def test_with_everything_enabled_no_rule_is_skipped_for_being_disabled(clean_result):
    disabled = [s for s in clean_result.rules_skipped if "disabled" in s.reason]
    assert not disabled, [s.rule_id for s in disabled]


def test_the_rules_that_do_not_run_are_only_missing_learned_inputs(clean_result):
    """A rule whose expectation was never learned must not run, because the only
    findings it could produce would be measured against a default the client never
    agreed to."""
    for skipped in clean_result.rules_skipped:
        assert "does not define" in skipped.reason, (
            f"{skipped.rule_id} was skipped for an unexpected reason: {skipped.reason}"
        )


# --------------------------------------------------------------------------------------
# The accounting that makes silence meaningful
# --------------------------------------------------------------------------------------


def test_the_summary_counts_are_all_zero(clean_result):
    summary = clean_result.summary
    assert summary["total"] == 0
    for severity in ("blocker", "major", "minor", "info"):
        assert summary[severity] == 0
    assert clean_result.worst_severity() is None


def test_the_exit_code_would_be_zero_at_every_threshold(clean_result):
    """The deck is usable as a pre-send gate: nothing at any severity."""
    for threshold in ("blocker", "major", "minor", "info"):
        assert not clean_result.exceeds(threshold)


def test_everything_not_checked_gives_a_reason(clean_result):
    """Section 13: never silently skip anything. An unchecked entry with no reason
    is indistinguishable from a pass, which is the failure mode that matters."""
    assert clean_result.unchecked, (
        "some shapes are legitimately unmeasurable on this deck -- an empty list "
        "here would suggest the rules stopped recording what they skipped"
    )
    for entry in clean_result.unchecked:
        assert entry.reason.strip(), f"{entry.rule_id} skipped a shape without saying why"
        assert entry.slide_index >= 1


def test_no_finding_is_suppressed_to_reach_silence(clean_result):
    """Silence has to come from the deck being clean, not from suppressions."""
    assert clean_result.suppressed == []


def test_every_rule_is_registered_documented_and_categorised():
    registry = load_all_rules()
    assert len(registry) == EXPECTED_RULE_COUNT, sorted(registry)
    for rule_id, rule in sorted(registry.items()):
        assert rule.category in CATEGORIES, rule_id
        assert rule.severity in ("blocker", "major", "minor", "info"), rule_id
        assert rule.summary, f"{rule_id} has no summary for `tieout rules`"
        assert rule.__doc__ and len(rule.__doc__.strip()) > 80, (
            f"{rule_id} must state what it measures and its false-positive mode"
        )


def test_the_catalogue_covers_every_category_at_the_specified_size():
    registry = load_all_rules()
    counts: dict[str, int] = {}
    for rule in registry.values():
        counts[rule.category] = counts.get(rule.category, 0) + 1
    assert counts == EXPECTED_PER_CATEGORY


# --------------------------------------------------------------------------------------
# Reports of a clean deck
# --------------------------------------------------------------------------------------


def test_the_console_report_renders_a_clean_deck(clean_result, capsys):
    from rich.console import Console

    console_report.render(clean_result, console=Console(width=100, force_terminal=False))
    printed = capsys.readouterr().out
    assert "No findings" in printed
    assert "not checked" in printed, "the reader must see what was not examined"


def test_the_quiet_console_report_is_one_line(clean_result, capsys):
    from rich.console import Console

    console_report.render(
        clean_result, console=Console(width=100, force_terminal=False), quiet=True
    )
    printed = capsys.readouterr().out.strip()
    assert "0 findings" in printed
    assert len(printed.splitlines()) <= 2


def test_the_json_report_is_well_formed_and_complete(clean_result):
    payload = json.loads(json_out.render(clean_result))
    assert set(payload) == {
        "schema_version",
        "deck",
        "client",
        "profile_version",
        "generated_at",
        "slide_count",
        "summary",
        "findings",
        "unchecked",
        "rules_skipped",
        "rules_run",
        "suppressed",
    }
    assert payload["findings"] == []
    assert payload["summary"]["total"] == 0
    assert payload["slide_count"] == 26
    assert payload["unchecked"], "the unchecked list must survive serialisation"
    for entry in payload["unchecked"]:
        assert entry["reason"]


def test_the_html_report_renders_a_clean_deck_and_lists_every_slide(
    clean_result, clean_deck
):
    markup = html_report.render(clean_result, clean_deck)
    assert "No findings" in markup
    for index in range(1, 27):
        assert f'href="#slide-{index}"' in markup, (
            f"slide {index} is missing from the sidebar; a sidebar listing only "
            f"slides with findings hides how much of the deck was examined"
        )


def test_the_html_report_reserves_its_thumbnail_slots(dirty_deck, derived_profile):
    """Section 2 puts slide images out of scope for this build; section 13 requires
    the template to accommodate them later without rework.

    Rendered against the dirty deck because the slots live on slides that have
    findings, and the clean deck has none.
    """
    clear_caches()
    markup = html_report.render(run_rules(dirty_deck, derived_profile), dirty_deck)
    assert 'class="thumb"' in markup
    assert "data-slide-width-pt" in markup, "the box needs the canvas size to scale to"
    assert "data-bbox" in markup, "overlays need each finding's bounding box"
    assert 'aspect-ratio: 16 / 9' in markup, "the box must hold its shape when empty"


# --------------------------------------------------------------------------------------
# The variants stay isolated
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "expected_rule"),
    [
        ("metadata", "HY-004"),
        ("comments", "HY-005"),
        ("external_rel", "HY-007"),
        ("wrong_slide_size", "BR-009"),
    ],
)
def test_each_single_defect_variant_triggers_only_its_own_rule(
    variant, expected_rule, variant_decks, derived_profile
):
    """These four defects cannot share a deck with the others, so each one having
    its own deck is only useful if that deck is otherwise clean."""
    deck = variant_decks[variant]
    clear_caches()
    result = run_rules(deck, derived_profile)
    triggered = {f.rule_id for f in result.findings}
    assert expected_rule in triggered, (
        f"the {variant} variant did not trigger {expected_rule}"
    )
    if variant != "wrong_slide_size":
        # A changed canvas size legitimately invalidates every geometric rule, so
        # only the package-level variants are expected to be otherwise clean.
        assert triggered == {expected_rule}, (
            f"the {variant} variant also triggered {sorted(triggered - {expected_rule})}"
        )
