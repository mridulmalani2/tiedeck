"""Layout rule tests.

Two tests per rule: it catches its own seeded defect, and it says nothing about
the clean deck. The second matters more. A layout rule that fires on correct
slides is worse than no rule at all, because a report full of false alignment
findings teaches the reader to skim past the real ones.

The dirty deck deliberately carries every co-existable defect at once, so a
layout rule legitimately reports collateral damage from another rule's seed --
the off-canvas box seeded for LO-001 also intrudes into the safe margin, and the
unapproved-typeface box seeded for BR-005 overlaps its neighbour. So detection
tests assert the seeded slide is *among* the findings rather than the only one,
and the clean-deck tests carry the burden of proving there is no noise.
"""

from __future__ import annotations

import pytest

from tests.conftest import assert_silent_on_clean, findings_for, slide_indices
from tieout.fixtures.spec import default_defects
from tieout.model.furniture import detect_furniture
from tieout.profile.schema import FontRole
from tieout.rules.base import run_rules

SEEDED = {d.rule_id: d.slide_index for d in default_defects() if d.variant is None}

LAYOUT_RULE_IDS = (
    "LO-001",
    "LO-002",
    "LO-003",
    "LO-004",
    "LO-005",
    "LO-006",
    "LO-007",
    "LO-008",
)


def _run(deck, profile, rule_id):
    return run_rules(deck, profile, include=[rule_id])


def _assert_catches(deck, profile, rule_id):
    result = _run(deck, profile, rule_id)
    assert rule_id in result.rules_run, (
        f"{rule_id} did not run: {[(s.rule_id, s.reason) for s in result.rules_skipped]}"
    )
    findings = findings_for(result, rule_id)
    assert SEEDED[rule_id] in slide_indices(findings), (
        f"{rule_id} missed its seeded defect on slide {SEEDED[rule_id]}; "
        f"reported {sorted(slide_indices(findings))}"
    )
    return findings


# --------------------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------------------


def test_every_layout_rule_is_registered():
    from tieout.rules.base import load_all_rules

    registry = load_all_rules()
    for rule_id in LAYOUT_RULE_IDS:
        assert rule_id in registry, f"{rule_id} is not registered"
        assert registry[rule_id].category == "layout"


def test_every_layout_rule_documents_itself():
    """Section 9 requires each rule to state what it measures and how it can be
    wrong. ``tieout rules`` surfaces both, so an undocumented rule is a gap the
    user sees."""
    from tieout.rules.base import load_all_rules

    registry = load_all_rules()
    for rule_id in LAYOUT_RULE_IDS:
        rule = registry[rule_id]
        assert rule.__doc__ and len(rule.__doc__.strip()) > 80, rule_id
        assert rule.summary, rule_id


# --------------------------------------------------------------------------------------
# LO-001 off canvas
# --------------------------------------------------------------------------------------


def test_lo001_catches_a_shape_off_the_canvas(dirty_deck, reference_profile):
    findings = _assert_catches(dirty_deck, reference_profile, "LO-001")
    seeded = [f for f in findings if f.slide_index == SEEDED["LO-001"]]
    assert any(f.measured for f in seeded), "the overhang should be measured in points"


def test_lo001_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    assert_silent_on_clean(_run(clean_deck, reference_profile, "LO-001"), "LO-001")


def test_lo001_accounts_for_rotation(clean_deck, reference_profile):
    """A rotated shape can overhang the canvas while its stored geometry sits
    inside it, so the rule must measure the rotated bounding box."""
    slide = clean_deck.slides[3]
    shape = next(s for s in slide.leaf_shapes() if s.width_pt > 200)
    original_rotation, original_left = shape.rotation, shape.left_pt
    try:
        shape.left_pt = clean_deck.width_pt - shape.width_pt - 2
        shape.rotation = 45.0
        findings = findings_for(_run(clean_deck, reference_profile, "LO-001"), "LO-001")
        assert slide.index in slide_indices(findings)
    finally:
        shape.rotation, shape.left_pt = original_rotation, original_left


# --------------------------------------------------------------------------------------
# LO-002 margin intrusion
# --------------------------------------------------------------------------------------


def test_lo002_catches_a_margin_intrusion(dirty_deck, reference_profile):
    _assert_catches(dirty_deck, reference_profile, "LO-002")


def test_lo002_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    assert_silent_on_clean(_run(clean_deck, reference_profile, "LO-002"), "LO-002")


def test_lo002_is_skipped_without_learned_margins(dirty_deck, reference_profile):
    reference_profile.layout.safe_margin_pt = {}
    result = _run(dirty_deck, reference_profile, "LO-002")
    assert result.findings == []
    assert any(s.rule_id == "LO-002" for s in result.rules_skipped)


def test_lo002_ignores_archetypes_with_no_learned_margin(dirty_deck, reference_profile):
    """An archetype absent from the profile is unchecked, not assumed."""
    reference_profile.layout.safe_margin_pt.pop("content", None)
    findings = findings_for(_run(dirty_deck, reference_profile, "LO-002"), "LO-002")
    assert SEEDED["LO-002"] not in slide_indices(findings)


# --------------------------------------------------------------------------------------
# LO-003 near-miss alignment
# --------------------------------------------------------------------------------------


def test_lo003_catches_a_column_off_its_grid_line(dirty_deck, reference_profile):
    _assert_catches(dirty_deck, reference_profile, "LO-003")


def test_lo003_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    """The whole reason the fixture is built on a 12pt grid.

    Every shape edge either coincides exactly or differs by at least 12pt, so
    there is no near miss to find. If this fails, the rule is over-reporting.
    """
    assert_silent_on_clean(_run(clean_deck, reference_profile, "LO-003"), "LO-003")


def test_lo003_needs_a_grid_to_judge_against(dirty_deck, reference_profile):
    """Without a learned grid a 3pt offset is indistinguishable from a deliberate
    one, which is section 8.3's point: off a real grid line it is a defect, off a
    one-off edge it is not."""
    reference_profile.layout.grid.columns_pt = []
    reference_profile.layout.grid.rows_pt = []
    result = _run(dirty_deck, reference_profile, "LO-003")
    assert findings_for(result, "LO-003") == []


def test_lo003_clusters_rather_than_reporting_every_pair(dirty_deck, reference_profile):
    """A row of misaligned blocks is one problem, not one per pair."""
    findings = findings_for(_run(dirty_deck, reference_profile, "LO-003"), "LO-003")
    per_slide = dict.fromkeys(slide_indices(findings), 0)
    for finding in findings:
        per_slide[finding.slide_index] += 1
    assert all(count <= 4 for count in per_slide.values()), per_slide


# --------------------------------------------------------------------------------------
# LO-004 overlap
# --------------------------------------------------------------------------------------


def test_lo004_catches_overlapping_text_shapes(dirty_deck, reference_profile):
    _assert_catches(dirty_deck, reference_profile, "LO-004")


def test_lo004_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    assert_silent_on_clean(_run(clean_deck, reference_profile, "LO-004"), "LO-004")


def test_lo004_tolerates_a_rule_beneath_text(clean_deck, reference_profile):
    """Hairline rules legitimately underlie text and must not be reported.

    The clean deck puts a 1.5pt navy rule directly beneath every title, so this
    is already covered by the silence test; asserting it explicitly documents the
    exemption so a future change to it is deliberate.
    """
    thin = [
        shape
        for slide in clean_deck.slides
        for shape in slide.leaf_shapes()
        if 0 < shape.height_pt < 3
    ]
    assert thin, "the fixture is supposed to contain hairline rules"
    assert findings_for(_run(clean_deck, reference_profile, "LO-004"), "LO-004") == []


# --------------------------------------------------------------------------------------
# LO-005 displaced recurring element
# --------------------------------------------------------------------------------------


def test_lo005_catches_a_displaced_footnote(dirty_deck, reference_profile):
    _assert_catches(dirty_deck, reference_profile, "LO-005")


def test_lo005_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    assert_silent_on_clean(_run(clean_deck, reference_profile, "LO-005"), "LO-005")


def test_lo005_is_skipped_without_learned_recurring_elements(
    dirty_deck, reference_profile
):
    reference_profile.layout.recurring = []
    result = _run(dirty_deck, reference_profile, "LO-005")
    assert result.findings == []
    assert any(s.rule_id == "LO-005" for s in result.rules_skipped)


# --------------------------------------------------------------------------------------
# LO-006 text overflow
# --------------------------------------------------------------------------------------


def test_lo006_is_disabled_by_default(clean_deck, reference_profile):
    """A glob selection is a filter, not an opt-in, so LO-006 stays off.

    Naming the rule exactly is a request to run it, which is the only way to
    reach an off-by-default rule without editing the profile; that path is
    covered by the detection test below.
    """
    result = run_rules(clean_deck, reference_profile, include=["LO-0*"])
    assert "LO-006" not in result.rules_run
    assert any(
        s.rule_id == "LO-006" and "default" in s.reason for s in result.rules_skipped
    )


def test_lo006_can_be_opted_into_through_the_profile(clean_deck, reference_profile):
    reference_profile.rules.enabled = ["LO-006"]
    result = run_rules(clean_deck, reference_profile, include=["LO-0*"])
    assert "LO-006" in result.rules_run


def test_lo006_either_reports_or_records_the_seeded_overflow(
    dirty_deck, reference_profile
):
    """LO-006 needs the real font file and must never guess.

    The seeded defect is set in the figure typeface, which resolves on this
    platform through a metric-compatible substitute, so a finding is expected.
    But the rule is specified to skip a shape whose font cannot be resolved and
    record it in ``unchecked`` instead, and on a host with no matching font that
    is the correct outcome. Both are accepted; what is not accepted is silence.
    """
    reference_profile.rules.disabled = []
    result = run_rules(dirty_deck, reference_profile, include=["LO-006"])
    seeded = SEEDED["LO-006"]
    reported = seeded in slide_indices(findings_for(result, "LO-006"))
    recorded = any(
        u.rule_id == "LO-006" and u.slide_index == seeded for u in result.unchecked
    )
    assert reported or recorded, (
        f"LO-006 neither reported nor recorded the overflow seeded on slide {seeded}"
    )


def test_lo006_never_reports_overflow_on_the_clean_deck(clean_deck, reference_profile):
    """Unchecked entries are fine here; findings are not."""
    reference_profile.rules.disabled = []
    result = run_rules(clean_deck, reference_profile, include=["LO-006"])
    assert findings_for(result, "LO-006") == [], [
        f.message for f in findings_for(result, "LO-006")
    ]


def test_lo006_records_unresolvable_fonts_rather_than_guessing(
    clean_deck, reference_profile
):
    reference_profile.rules.disabled = []
    result = run_rules(clean_deck, reference_profile, include=["LO-006"])
    assert findings_for(result, "LO-006") == []
    # Every shape it declined to measure must be accounted for.
    for entry in result.unchecked:
        if entry.rule_id == "LO-006":
            assert entry.reason, "an unchecked entry must say why"


# --------------------------------------------------------------------------------------
# LO-007 font size band
# --------------------------------------------------------------------------------------


def test_lo007_catches_a_size_outside_the_band(dirty_deck, reference_profile):
    findings = _assert_catches(dirty_deck, reference_profile, "LO-007")
    seeded = [f for f in findings if f.slide_index == SEEDED["LO-007"]]
    assert any("18" in (f.measured or "") for f in seeded), [
        f.measured for f in seeded
    ]


def test_lo007_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    assert_silent_on_clean(_run(clean_deck, reference_profile, "LO-007"), "LO-007")


def test_lo007_does_not_check_a_role_that_was_never_learned(
    clean_deck, reference_profile
):
    """``chart_label`` is in ``not_learned`` for the reference deck, so chart text
    must not be measured against an invented band."""
    assert "chart_label" not in reference_profile.brand.fonts.roles
    assert findings_for(_run(clean_deck, reference_profile, "LO-007"), "LO-007") == []


def test_lo007_respects_an_exact_allowed_set(dirty_deck, reference_profile):
    reference_profile.brand.fonts.roles["body"] = FontRole(exact_pt=[11.0])
    findings = findings_for(_run(dirty_deck, reference_profile, "LO-007"), "LO-007")
    assert findings, "an exact set of 11pt should reject the deck's 10 and 12pt body"


def test_lo007_is_skipped_without_learned_roles(dirty_deck, reference_profile):
    reference_profile.brand.fonts.roles = {}
    result = _run(dirty_deck, reference_profile, "LO-007")
    assert result.findings == []
    assert any(s.rule_id == "LO-007" for s in result.rules_skipped)


# --------------------------------------------------------------------------------------
# LO-008 gutters
# --------------------------------------------------------------------------------------


def test_lo008_catches_uneven_gutters(dirty_deck, reference_profile):
    _assert_catches(dirty_deck, reference_profile, "LO-008")


def test_lo008_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    """The clean deck mixes two, three and four column rows. Each row is evenly
    guttered within itself, which is what the rule measures; the deck has no
    single deck-wide gutter, and the rule must not expect one."""
    assert_silent_on_clean(_run(clean_deck, reference_profile, "LO-008"), "LO-008")


# --------------------------------------------------------------------------------------
# The false-positive guard
# --------------------------------------------------------------------------------------


def test_layout_category_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    """The single most important assertion in this module.

    Every layout rule, enabled ones and LO-006 alike, run against the deck the
    profile describes. Anything reported here is a false positive.
    """
    reference_profile.rules.disabled = []
    reference_profile.rules.enabled = ["LO-006"]
    result = run_rules(clean_deck, reference_profile, include=["LO-*"])
    assert result.findings == [], "; ".join(
        f"slide {f.slide_index} {f.rule_id}: {f.message}" for f in result.findings
    )


def test_layout_rules_exclude_furniture(clean_deck, reference_profile):
    """The logo, page number and confidentiality line sit outside the content
    frame by design, so admitting them would make LO-002 report every slide."""
    furniture = detect_furniture(clean_deck, reference_profile)
    assert furniture.logo_sha1s, "the fixture is supposed to carry a logo"
    assert furniture.page_numbers, "the fixture is supposed to carry page numbers"
    result = run_rules(clean_deck, reference_profile, include=["LO-002", "LO-003"])
    assert result.findings == []


@pytest.mark.parametrize("rule_id", LAYOUT_RULE_IDS)
def test_every_layout_rule_reaches_a_verdict_on_the_dirty_deck(
    rule_id, dirty_deck, reference_profile
):
    """A rule must either run, or say why it did not. Never both silent and absent."""
    reference_profile.rules.disabled = []
    reference_profile.rules.enabled = [rule_id]
    result = run_rules(dirty_deck, reference_profile, include=[rule_id])
    accounted = rule_id in result.rules_run or any(
        s.rule_id == rule_id for s in result.rules_skipped
    )
    assert accounted, f"{rule_id} neither ran nor reported why not"
