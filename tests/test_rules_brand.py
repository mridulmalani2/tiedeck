"""Brand rule tests.

Two tests per rule, and they carry unequal weight. The seeded-defect test proves
the rule measures the thing it claims to; the clean-deck test proves it says
nothing about correct work. A rule that passes the first and fails the second is
worse than no rule at all, because it teaches the user to skim the report.

The seeded slide for each rule is read from :func:`default_defects` rather than
hardcoded, so the fixture and the tests cannot drift apart.
"""

from __future__ import annotations

import pytest

from tests.conftest import assert_silent_on_clean, findings_for, slide_indices
from tieout.fixtures.spec import ReferenceSpec
from tieout.profile.schema import NotLearned
from tieout.rules.base import REGISTRY, clear_caches, load_all_rules, run_rules

BRAND_RULE_IDS = [f"BR-{n:03d}" for n in range(1, 11)]


@pytest.fixture(autouse=True)
def _isolate_furniture_cache():
    """Drop memoised furniture between tests.

    The cache is keyed on ``id(deck), id(profile)`` and ``reference_profile`` is
    function-scoped, so a collected profile's id can be handed to the next one and
    a test would silently measure against the previous test's furniture.
    """
    clear_caches()
    yield
    clear_caches()


def seeded_slide(spec: ReferenceSpec, rule_id: str) -> int:
    """The slide the fixture spec seeds this rule's defect on."""
    for defect in spec.defects:
        if defect.rule_id == rule_id and defect.slide_index is not None:
            return defect.slide_index
    raise AssertionError(f"{rule_id} has no seeded slide in the fixture spec")


def check(deck, profile, rule_id):
    return run_rules(deck, profile, include=[rule_id])


# --------------------------------------------------------------------------------------
# BR-001 logo missing
# --------------------------------------------------------------------------------------


def test_br001_catches_missing_logo(dirty_deck, reference_profile, spec):
    result = check(dirty_deck, reference_profile, "BR-001")
    findings = findings_for(result, "BR-001")

    # Slide 4 has the logo deleted. Slide 11 carries the low-resolution re-export
    # seeded for HY-008, which hashes differently from the approved asset and so
    # reads as missing -- the rule's documented false-positive mode.
    assert slide_indices(findings) == {seeded_slide(spec, "BR-001"), 11}
    assert all(f.severity == "blocker" for f in findings)
    assert all(f.expected_provenance for f in findings)


def test_br001_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-001"), "BR-001")


def test_br001_does_not_measure_exempt_archetypes(clean_deck, reference_profile, spec):
    """The title and disclaimer slides have no logo and must not be reported."""
    result = check(clean_deck, reference_profile, "BR-001")
    assert result.findings == []
    exempt = {s.index for s in clean_deck.slides if s.archetype in ("title", "disclaimer")}
    assert exempt, "the fixture deck should contain logo-exempt archetypes"
    assert not slide_indices(findings_for(result, "BR-001")) & exempt


# --------------------------------------------------------------------------------------
# BR-002 logo position
# --------------------------------------------------------------------------------------


def test_br002_catches_shifted_logo(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-002"), "BR-002")
    assert slide_indices(findings) == {seeded_slide(spec, "BR-002")}
    (finding,) = findings
    assert "18.0pt left" in finding.message
    assert finding.expected_provenance


def test_br002_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-002"), "BR-002")


def test_br002_skipped_when_no_logo_was_learned(dirty_deck, reference_profile):
    """A rule with no learned expectation must not fall back to a default."""
    reference_profile.brand.logo = None
    result = run_rules(dirty_deck, reference_profile, include=["BR-002"])

    assert result.findings == []
    skipped = {s.rule_id: s.reason for s in result.rules_skipped}
    assert "BR-002" in skipped
    assert "brand.logo" in skipped["BR-002"]


def test_br002_skipped_when_logo_is_marked_not_learned(dirty_deck, reference_profile):
    reference_profile.not_learned.append(
        NotLearned(key="brand.logo", reason="the reference deck had no repeated image")
    )
    result = run_rules(dirty_deck, reference_profile, include=["BR-002"])

    assert result.findings == []
    assert "BR-002" in {s.rule_id for s in result.rules_skipped}


# --------------------------------------------------------------------------------------
# BR-003 logo size and aspect
# --------------------------------------------------------------------------------------


def test_br003_catches_stretched_logo(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-003"), "BR-003")
    assert slide_indices(findings) == {seeded_slide(spec, "BR-003")}
    (finding,) = findings
    assert "distorted" in finding.message
    assert finding.expected_provenance


def test_br003_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-003"), "BR-003")


def test_br003_records_unchecked_when_the_logo_is_absent(dirty_deck, reference_profile):
    """Aspect distortion cannot be measured with no logo, and that is recorded."""
    result = check(dirty_deck, reference_profile, "BR-003")
    unchecked = [u for u in result.unchecked if u.rule_id == "BR-003"]
    assert {u.slide_index for u in unchecked} == {4, 11}
    assert all("cannot be measured" in u.reason for u in unchecked)


# --------------------------------------------------------------------------------------
# BR-004 palette
# --------------------------------------------------------------------------------------


def test_br004_catches_off_palette_fill(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-004"), "BR-004")
    assert slide_indices(findings) == {seeded_slide(spec, "BR-004")}
    (finding,) = findings
    assert "#2E8B7A" in finding.message
    # The nearest palette colour and the Delta-E are both reported.
    assert "#A6A6A6" in finding.message
    assert "Delta-E" in finding.message
    assert "1 shape" in finding.message
    assert finding.expected_provenance


def test_br004_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-004"), "BR-004")


def test_br004_tolerates_a_near_miss_colour(clean_deck, reference_profile):
    """A colour a designer cannot distinguish is not a defect.

    The house navy is dropped from the palette and replaced with a value one
    Delta-E step away, which must not be reported at the learned tolerance.
    """
    palette = list(reference_profile.brand.palette_hex)
    assert "#1F3864" in palette
    palette[palette.index("#1F3864")] = "#1F3963"
    reference_profile.brand.palette_hex = palette

    assert check(clean_deck, reference_profile, "BR-004").findings == []


def test_br004_clusters_by_slide_and_colour(clean_deck, reference_profile):
    """One finding per slide per colour, not one per shape.

    Removing the rule colour from the palette makes every horizontal rule in the
    deck off-palette. Each affected slide must produce exactly one finding.
    """
    reference_profile.brand.palette_hex = [
        entry for entry in reference_profile.brand.palette_hex if entry != "#A6A6A6"
    ]
    findings = findings_for(check(clean_deck, reference_profile, "BR-004"), "BR-004")

    assert findings, "removing a palette entry should produce findings"
    per_slide = [f for f in findings if f.slide_index == findings[0].slide_index]
    assert len(per_slide) == 1
    assert "shape" in per_slide[0].message


# --------------------------------------------------------------------------------------
# BR-005 typefaces
# --------------------------------------------------------------------------------------


def test_br005_catches_unapproved_typeface(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-005"), "BR-005")
    seeded = seeded_slide(spec, "BR-005")
    assert seeded in slide_indices(findings)
    # Slide 16 carries the non-standard face seeded for HY-009, which is equally
    # unapproved; nothing else in the deck may be reported.
    assert slide_indices(findings) == {seeded, 16}
    assert any("Comic Sans MS" in f.message for f in findings)
    assert all(f.severity == "blocker" for f in findings)


def test_br005_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-005"), "BR-005")


def test_br005_matches_the_allowed_set_case_insensitively(clean_deck, reference_profile):
    reference_profile.brand.fonts.allowed = ["gill sans mt", "ARIAL"]
    assert check(clean_deck, reference_profile, "BR-005").findings == []


# --------------------------------------------------------------------------------------
# BR-006 page numbers
# --------------------------------------------------------------------------------------


def test_br006_catches_malformed_page_number(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-006"), "BR-006")
    assert slide_indices(findings) == {seeded_slide(spec, "BR-006")}
    (finding,) = findings
    assert "Page 15 of 26" in finding.message
    assert r"^\d+$" in finding.message
    assert finding.expected_provenance


def test_br006_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-006"), "BR-006")


def test_br006_catches_a_page_number_out_of_position(clean_deck, reference_profile):
    """Moving the learned box reports every page number as out of position."""
    box = reference_profile.brand.footer.page_number.box_pt
    box.left = box.left + 120.0
    findings = findings_for(check(clean_deck, reference_profile, "BR-006"), "BR-006")

    assert findings
    assert all("expected position" in f.message for f in findings)
    # One finding per slide, even though position and pattern are checked together.
    assert len(findings) == len(slide_indices(findings))


def test_br006_skipped_when_no_page_number_was_learned(dirty_deck, reference_profile):
    reference_profile.brand.footer.page_number = None
    result = run_rules(dirty_deck, reference_profile, include=["BR-006"])

    assert result.findings == []
    assert "BR-006" in {s.rule_id for s in result.rules_skipped}


# --------------------------------------------------------------------------------------
# BR-007 page-number sequence
# --------------------------------------------------------------------------------------


def test_br007_catches_broken_ascent(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-007"), "BR-007")
    assert slide_indices(findings) == {seeded_slide(spec, "BR-007")}
    # One finding for the whole deck: the first break, not every slide after it.
    assert len(findings) == 1
    assert findings[0].measured == "3"


def test_br007_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-007"), "BR-007")


def test_br007_gated_on_must_ascend(dirty_deck, reference_profile):
    reference_profile.brand.footer.page_number.must_ascend = False
    result = check(dirty_deck, reference_profile, "BR-007")

    assert result.findings == []
    assert "BR-007" in {s.rule_id for s in result.rules_skipped}


# --------------------------------------------------------------------------------------
# BR-008 title geometry
# --------------------------------------------------------------------------------------


def test_br008_catches_title_drift(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-008"), "BR-008")
    assert slide_indices(findings) == {seeded_slide(spec, "BR-008")}
    (finding,) = findings
    assert finding.severity == "minor"
    assert "left +24.0pt" in finding.message


def test_br008_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-008"), "BR-008")


def test_br008_respects_the_learned_tolerance(dirty_deck, reference_profile):
    """A tolerance wider than the seeded drift silences the rule."""
    reference_profile.brand.title_geometry_tolerance_pt = 1000.0
    assert check(dirty_deck, reference_profile, "BR-008").findings == []


# --------------------------------------------------------------------------------------
# BR-009 slide dimensions
# --------------------------------------------------------------------------------------


def test_br009_catches_wrong_slide_size(variant_decks, reference_profile):
    deck = variant_decks["wrong_slide_size"]
    findings = findings_for(check(deck, reference_profile, "BR-009"), "BR-009")

    assert len(findings) == 1
    (finding,) = findings
    assert finding.slide_index == 1
    assert finding.severity == "blocker"
    assert finding.measured == "720x540pt"
    assert finding.expected_provenance


def test_br009_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-009"), "BR-009")


# --------------------------------------------------------------------------------------
# BR-010 boilerplate
# --------------------------------------------------------------------------------------


def test_br010_catches_missing_confidentiality_line(dirty_deck, reference_profile, spec):
    findings = findings_for(check(dirty_deck, reference_profile, "BR-010"), "BR-010")
    assert slide_indices(findings) == {seeded_slide(spec, "BR-010")}
    (finding,) = findings
    assert "confidentiality marking" in finding.message
    assert finding.expected_provenance


def test_br010_silent_on_clean(clean_deck, reference_profile):
    assert_silent_on_clean(check(clean_deck, reference_profile, "BR-010"), "BR-010")


def test_br010_matching_is_normalised(clean_deck, reference_profile):
    """A footer differing only in case and quote style is still present."""
    entry = reference_profile.brand.footer.boilerplate[0]
    entry.text = "  STRICTLY   private and Confidential "
    assert check(clean_deck, reference_profile, "BR-010").findings == []

    entry.match_normalised = False
    assert check(clean_deck, reference_profile, "BR-010").findings


# --------------------------------------------------------------------------------------
# The category as a whole
# --------------------------------------------------------------------------------------


def test_brand_category_is_silent_on_the_clean_deck(clean_deck, reference_profile):
    result = run_rules(clean_deck, reference_profile, include=["BR-*"])

    noise = "; ".join(
        f"{f.rule_id} slide {f.slide_index}: {f.message}" for f in result.findings[:10]
    )
    assert result.findings == [], f"the brand rules must say nothing about correct work: {noise}"
    assert result.unchecked == [], "nothing on the clean deck should be unmeasurable"
    assert result.rules_run == BRAND_RULE_IDS
    assert result.rules_skipped == []


def test_every_brand_rule_documents_itself():
    """``tieout rules`` surfaces these, and the specification requires them."""
    load_all_rules()
    for rule_id in BRAND_RULE_IDS:
        rule = REGISTRY[rule_id]
        assert rule.category == "brand"
        assert rule.summary, f"{rule_id} has no summary"
        assert rule.requires, f"{rule_id} declares no profile requirement"
        doc = rule.__doc__ or ""
        assert "Measures" in doc or "Measures:" in doc, f"{rule_id} does not say what it measures"
        assert "false positive" in doc, f"{rule_id} does not state its false-positive mode"
