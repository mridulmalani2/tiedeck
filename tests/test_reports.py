"""The three reports.

All three answer the same question -- what is wrong with this deck, and why does
TieOut think so -- for three different readers: a banker at a terminal, a
pipeline, and whoever the HTML gets emailed to.

The recurring assertion is that none of them lets silence pass for a pass. A
report that omits what it could not check is indistinguishable from one where
everything was fine, and that ambiguity is what would make the tool unusable as
a pre-send gate.
"""

from __future__ import annotations

import json
import re

import pytest
from rich.console import Console

from tieout.model.deck import ShapeRef
from tieout.profile.schema import Confidence, Severity
from tieout.report import console as console_report
from tieout.report import html as html_report
from tieout.report import json_out
from tieout.rules.base import (
    AuditResult,
    Finding,
    RuleSkipped,
    Unchecked,
    clear_caches,
    cluster_findings,
    run_rules,
)


def _console() -> Console:
    return Console(width=120, force_terminal=False, soft_wrap=False)


@pytest.fixture(scope="module")
def dirty_result(dirty_deck):
    from tieout.learn import learn_from_decks

    clear_caches()
    profile = learn_from_decks([dirty_deck], "reports").profile
    clear_caches()
    return run_rules(dirty_deck, profile)


def _synthetic() -> AuditResult:
    """A hand-built result, so the report tests do not depend on which rules fire."""
    result = AuditResult(
        deck_path="/decks/board.pptx",
        client="acme",
        profile_version=3,
        generated_at="2026-09-14T09:00:00+00:00",
        slide_count=4,
    )
    result.findings = [
        Finding(
            rule_id="BR-002",
            category="brand",
            severity="major",
            confidence="high",
            where=ShapeRef(2, 42, "Logo"),
            message="logo sits 18pt left of its expected position",
            measured="left 834pt",
            expected="left 852pt",
            expected_provenance="observed on 18 of 26 slides in your reference deck",
            bbox_pt=(834.0, 24.0, 72.0, 24.0),
        ),
        Finding(
            rule_id="HY-004",
            category="hygiene",
            severity="blocker",
            confidence="high",
            where=1,
            message="document properties name the author",
            measured="creator='A. Analyst'",
            expected="empty",
            expected_provenance="default, not inferred",
        ),
        Finding(
            rule_id="TY-001",
            category="typography",
            severity="minor",
            confidence="medium",
            where=ShapeRef(2, 7, "Column 1", ("Group 3",)),
            message="straight apostrophe against a curly convention",
        ),
    ]
    result.unchecked = [
        Unchecked("LO-006", ShapeRef(3, 9, "Body"), "the font file could not be resolved")
    ]
    result.rules_skipped = [RuleSkipped("TY-009", "disabled in the profile")]
    result.rules_run = ["BR-002", "HY-004", "TY-001"]
    return result


# --------------------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------------------


def test_the_console_report_groups_by_slide(capsys):
    """The order someone fixes a deck in: open slide 2, fix everything, move on."""
    console_report.render(_synthetic(), console=_console())
    printed = capsys.readouterr().out
    assert "slide 1" in printed and "slide 2" in printed
    assert printed.index("slide 1") < printed.index("slide 2")


def test_the_console_report_shows_the_measurement_and_the_expectation(capsys):
    console_report.render(_synthetic(), console=_console())
    printed = capsys.readouterr().out.replace("\n", " ")
    # Asserted on the numbers rather than the full strings: the
    # measured/expected column folds, so "left 834pt" can be split across lines.
    assert "834" in printed
    assert "852" in printed


def test_the_console_report_shows_why_a_value_was_expected(capsys):
    """The thing that makes a finding arguable-with rather than arbitrary."""
    console_report.render(_synthetic(), console=_console())
    assert "18 of 26 slides" in capsys.readouterr().out


def test_provenance_can_be_suppressed_for_a_terser_report(capsys):
    console_report.render(_synthetic(), console=_console(), show_provenance=False)
    assert "18 of 26 slides" not in capsys.readouterr().out


def test_a_grouped_shape_is_named_with_its_group_path(capsys):
    console_report.render(_synthetic(), console=_console())
    assert "Group 3" in capsys.readouterr().out


def test_the_console_footer_accounts_for_everything(capsys):
    console_report.render(_synthetic(), console=_console())
    printed = capsys.readouterr().out
    assert "rules not run" in printed
    assert "TY-009" in printed
    assert "not checked" in printed
    assert "font file could not be resolved" in printed


def test_the_console_summary_counts_each_severity(capsys):
    console_report.render(_synthetic(), console=_console())
    printed = capsys.readouterr().out
    assert "1 blocker" in printed
    assert "1 major" in printed
    assert "1 minor" in printed


def _many() -> AuditResult:
    """Enough findings that a reader needs telling where to start."""
    result = _synthetic()
    for index in range(4, 12):
        result.findings.append(
            Finding(
                rule_id="TY-002",
                category="typography",
                severity="minor",
                confidence="high",
                where=ShapeRef(3, index, f"Box {index}"),
                message="double space",
            )
        )
    result.findings.append(
        Finding(
            rule_id="CO-003",
            category="consistency",
            severity="major",
            confidence="medium",
            where=ShapeRef(4, 99, "Financial table"),
            message="'Total' states 9,900 but the rows above it sum to 9,828.5",
            measured="9,900",
            expected="9,828.5 (+/-2.5 rounding)",
            remedy="Correct the total, or the rows it sums",
        )
    )
    return result


def test_the_report_says_what_to_fix_first(capsys):
    """Grouping by slide is the order someone fixes in, not the order they decide
    in. What stops the deck going out comes before everything else."""
    console_report.render(_many(), console=_console())
    printed = capsys.readouterr().out
    assert "fix first" in printed
    # The block ends where the first slide table begins: a title line reading
    # "slide N" on its own. "slide 1" also appears *inside* the block, on the
    # blocker's own line, so a plain search would cut the block short.
    first_table = re.search(r"^slide \d+\s*$", printed, re.MULTILINE)
    assert first_table is not None
    block = printed[printed.index("fix first") : first_table.start()]
    assert "HY-004" in block, "the blocker leads"
    assert "CO-003" in block and "BR-002" in block, "majors follow"
    assert "TY-002" not in block, "minors wait for the slide tables"
    assert block.index("HY-004") < block.index("BR-002"), "severity order"
    assert "Correct the total" in block, "the remedy is right there"


def test_a_short_report_is_its_own_triage(capsys):
    console_report.render(_synthetic(), console=_console())
    assert "fix first" not in capsys.readouterr().out


def test_a_narrow_terminal_gets_stacked_findings_that_do_not_fold(capsys):
    """At 80 columns the five-column table shrank every cell and the measured
    and expected values folded into fragments of two or three characters."""
    narrow = Console(width=80, force_terminal=False, soft_wrap=False)
    console_report.render(_synthetic(), console=narrow)
    printed = capsys.readouterr().out
    assert "left 834pt" in printed and "left 852pt" in printed, "values whole, not folded"
    assert "because observed on 18 of 26 slides" in printed, "evidence labelled as such"


def test_provenance_is_labelled_so_it_cannot_read_as_the_claim(capsys):
    console_report.render(_synthetic(), console=_console())
    assert "because " in capsys.readouterr().out


def test_suppressed_findings_are_acknowledged_not_hidden(capsys):
    result = _synthetic()
    result.suppressed = [result.findings.pop()]
    console_report.render(result, console=_console())
    assert "suppressed" in capsys.readouterr().out


def test_unchecked_entries_are_condensed_rather_than_listed_one_by_one(capsys):
    """A deck with an unresolvable font produces one entry per shape, and printing
    two hundred of them buries the summary that matters."""
    result = _synthetic()
    result.unchecked = [
        Unchecked("LO-006", ShapeRef(index, 1, "Body"), "the font file could not be resolved")
        for index in range(1, 40)
    ]
    console_report.render(result, console=_console())
    printed = capsys.readouterr().out
    assert "39 shapes on 39 slides" in printed


def test_the_rules_table_lists_every_rule_with_its_state(capsys):
    console_report.render_rules_table(console=_console(), client="acme")
    printed = capsys.readouterr().out
    for rule_id in ("BR-001", "LO-006", "TY-009", "HY-009"):
        assert rule_id in printed
    assert "off" in printed, "the off-by-default rules must be marked"
    assert "acme" in printed


def test_the_real_dirty_deck_renders_without_error(dirty_result, capsys):
    console_report.render(dirty_result, console=_console())
    printed = capsys.readouterr().out
    assert "finding(s)" in printed
    assert dirty_result.findings


# --------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------


def test_the_json_schema_is_exactly_the_documented_key_set():
    payload = json.loads(json_out.render(_synthetic()))
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


def test_each_json_finding_carries_the_documented_fields():
    payload = json.loads(json_out.render(_synthetic()))
    finding = payload["findings"][0]
    assert set(finding) == {
        "rule_id",
        "category",
        "severity",
        "confidence",
        "slide_index",
        "shape",
        "message",
        "measured",
        "expected",
        "expected_provenance",
        "bbox_pt",
    }


def test_a_shape_finding_carries_its_identity_and_a_slide_finding_does_not():
    payload = json.loads(json_out.render(_synthetic()))
    by_rule = {f["rule_id"]: f for f in payload["findings"]}
    assert by_rule["HY-004"]["shape"] is None, "a deck-level finding has no shape"
    logo = by_rule["BR-002"]["shape"]
    assert logo["name"] == "Logo"
    assert logo["shape_id"] == 42
    assert by_rule["TY-001"]["shape"]["group_path"] == ["Group 3"]


def test_the_bounding_box_survives_as_a_list():
    payload = json.loads(json_out.render(_synthetic()))
    box = next(f for f in payload["findings"] if f["rule_id"] == "BR-002")["bbox_pt"]
    assert box == [834.0, 24.0, 72.0, 24.0]


def test_json_findings_are_ordered_by_slide():
    payload = json.loads(json_out.render(_synthetic()))
    indices = [f["slide_index"] for f in payload["findings"]]
    assert indices == sorted(indices)


def test_json_records_what_was_not_checked_and_why():
    payload = json.loads(json_out.render(_synthetic()))
    assert payload["unchecked"][0]["reason"]
    assert payload["unchecked"][0]["rule_id"] == "LO-006"
    assert payload["rules_skipped"][0]["rule_id"] == "TY-009"


def test_json_is_written_to_disk_with_its_directory_created(tmp_path):
    target = tmp_path / "nested" / "report.json"
    assert json_out.write(_synthetic(), target) == target
    assert json.loads(target.read_text())["client"] == "acme"


# --------------------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------------------


def test_the_html_report_fetches_nothing_at_view_time():
    """The artefact most likely to be emailed outside the firm. A stylesheet it
    fetched would break the air-gap promise exactly where it matters most."""
    markup = html_report.render(_synthetic())
    for forbidden in (
        "http://",
        "https://",
        "//cdn",
        "fonts.googleapis",
        "fonts.gstatic",
        "<script",
        "@import url",
    ):
        assert forbidden not in markup


def test_the_html_report_shows_every_finding_with_its_provenance():
    markup = html_report.render(_synthetic())
    assert "logo sits 18pt left of its expected position" in markup
    assert "Why this is expected" in markup
    assert "18 of 26 slides in your reference deck" in markup


def test_the_html_report_escapes_content_rather_than_trusting_it():
    """Shape names come from the deck, so they are untrusted input to the
    template."""
    result = _synthetic()
    result.findings = [
        Finding(
            rule_id="BR-005",
            category="brand",
            severity="blocker",
            confidence="high",
            where=ShapeRef(1, 1, "<script>alert(1)</script>"),
            message="unapproved typeface in <b>bold</b>",
        )
    ]
    markup = html_report.render(result)
    assert "<script>alert(1)</script>" not in markup
    assert "&lt;script&gt;" in markup


def test_the_html_sidebar_lists_clean_slides_too(clean_deck):
    """A sidebar showing only slides with findings hides how much was examined."""
    result = _synthetic()
    markup = html_report.render(result, clean_deck)
    assert markup.count('class="nav-item') == clean_deck.slide_count


def test_the_html_report_records_what_was_not_checked():
    markup = html_report.render(_synthetic())
    assert "Not checked" in markup
    assert "Rules not run" in markup
    assert "font file could not be resolved" in markup


def test_the_html_report_carries_the_hooks_for_later_slide_images(dirty_result, dirty_deck):
    markup = html_report.render(dirty_result, dirty_deck)
    assert 'class="thumb"' in markup
    assert "data-slide-width-pt" in markup
    assert "data-bbox" in markup


def test_the_html_report_adapts_to_dark_mode_and_to_a_phone():
    markup = html_report.render(_synthetic())
    assert "prefers-color-scheme: dark" in markup
    assert "@media (max-width: 900px)" in markup
    assert "@media print" in markup


def test_the_html_report_is_written_to_disk(tmp_path):
    target = tmp_path / "nested" / "report.html"
    assert html_report.write(_synthetic(), target) == target
    assert "acme" in target.read_text()


def test_a_clean_deck_renders_a_clean_html_report(clean_deck):
    result = AuditResult(
        deck_path="/decks/clean.pptx",
        client="acme",
        profile_version=1,
        generated_at="2026-09-14",
        slide_count=clean_deck.slide_count,
        rules_run=["BR-001"],
    )
    markup = html_report.render(result, clean_deck)
    assert "No findings" in markup


# --------------------------------------------------------------------------------------
# Clustering, which every report relies on
# --------------------------------------------------------------------------------------


def test_findings_sharing_a_slide_and_a_rule_are_collapsed():
    """Section 16: a row of five misaligned cards is one problem, and reporting it
    five times trains the reader to skim past the report."""
    findings = [
        Finding(
            rule_id="LO-003",
            category="layout",
            severity="minor",
            confidence="high",
            where=ShapeRef(4, index, f"Card {index}"),
            message="3pt off the grid line at 492pt",
        )
        for index in range(5)
    ]
    clustered = cluster_findings(findings)
    assert len(clustered) == 1
    assert "and 4 more on this slide" in clustered[0].message


def test_clustering_keeps_the_worst_severity_in_the_group():
    severities: tuple[Severity, ...] = ("minor", "blocker", "major")
    findings = [
        Finding(
            rule_id="LO-004",
            category="layout",
            severity=severity,
            confidence="high",
            where=ShapeRef(4, index, "Shape"),
            message="overlap",
        )
        for index, severity in enumerate(severities)
    ]
    assert cluster_findings(findings)[0].severity == "blocker"


def test_clustering_does_not_merge_across_slides_or_rules():
    findings = [
        Finding(
            rule_id=rule,
            category="layout",
            severity="minor",
            confidence="high",
            where=ShapeRef(slide, 1, "Shape"),
            message="something",
        )
        for rule in ("LO-003", "LO-004")
        for slide in (1, 2)
    ]
    assert len(cluster_findings(findings)) == 4


def test_a_lone_finding_is_left_exactly_as_it_was():
    only = _synthetic().findings[0]
    assert cluster_findings([only]) == [only]



# --------------------------------------------------------------------------------------
# Confidence reaches the gate
# --------------------------------------------------------------------------------------


def _finding(severity: Severity, confidence: Confidence) -> Finding:
    return Finding(
        rule_id="LO-004",
        category="layout",
        severity=severity,
        confidence=confidence,
        where=ShapeRef(1, 1, "Box"),
        message="overlap",
    )


def test_a_low_confidence_finding_is_reported_but_does_not_gate():
    """Severity says how bad if true; confidence says how likely to be true.
    A gate that reads only the first fails a deck on a heuristic as readily
    as on arithmetic."""
    result = _synthetic()
    result.findings = [_finding("blocker", "low")]
    assert not result.exceeds("blocker"), "low confidence never gates by default"
    assert result.exceeds("blocker", min_confidence="low"), "unless asked to"


def test_the_default_gate_admits_medium_confidence():
    result = _synthetic()
    result.findings = [_finding("major", "medium")]
    assert result.exceeds("major")
    assert not result.exceeds("major", min_confidence="high")


def test_the_console_marks_a_finding_that_is_less_than_sure(capsys):
    result = _synthetic()
    result.findings = [_finding("major", "medium")]
    console_report.render(result, console=_console())
    assert "(medium confidence)" in capsys.readouterr().out
