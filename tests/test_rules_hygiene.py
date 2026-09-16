"""Hygiene rule tests.

Every rule gets the same pair of tests: it catches the defect the fixture
generator seeded for it, and it says nothing at all about the clean deck. The
second half of the pair is the one that matters -- a rule that fires on correct
slides trains the user to skim past the report, which is worse than not shipping
the rule.

The seeded slide indices are read from :func:`default_defects` rather than
written out here, so a change to the fixture specification fails the test that
depends on it instead of silently drifting away from it.
"""

from __future__ import annotations

import pytest

from tests.conftest import assert_silent_on_clean, findings_for, slide_indices
from tieout.fixtures.spec import ReferenceSpec, SeededDefect
from tieout.model.deck import DeckModel
from tieout.profile.schema import Profile
from tieout.rules.base import AuditResult, run_rules
from tieout.rules.hygiene import STANDARD_SYSTEM_FONTS, _font_base_name


def seeded(spec: ReferenceSpec, rule_id: str) -> SeededDefect:
    """The seeded defect for one rule, so tests never hardcode a slide index."""
    for defect in spec.defects:
        if defect.rule_id == rule_id:
            return defect
    raise AssertionError(f"no seeded defect for {rule_id}")


def seeded_slide(spec: ReferenceSpec, rule_id: str) -> int:
    index = seeded(spec, rule_id).slide_index
    assert index is not None, f"{rule_id} has no seeded slide index"
    return index


def check(deck: DeckModel, profile: Profile, rule_id: str) -> AuditResult:
    return run_rules(deck, profile, include=[rule_id])


def assert_caught_on(
    result: AuditResult, rule_id: str, slide_index: int
) -> None:
    """One finding from this rule, on this slide, and nowhere else."""
    findings = findings_for(result, rule_id)
    assert len(findings) == 1, (
        f"{rule_id} produced {len(findings)} findings, expected exactly one: "
        + "; ".join(f"slide {f.slide_index}: {f.message}" for f in findings)
    )
    assert slide_indices(findings) == {slide_index}
    assert findings[0].expected_provenance, f"{rule_id} reported without provenance"


# --------------------------------------------------------------------------------------
# HY-001 placeholder markers
# --------------------------------------------------------------------------------------


def test_hy001_catches_placeholder_marker(
    dirty_deck: DeckModel, reference_profile: Profile, spec: ReferenceSpec
) -> None:
    result = check(dirty_deck, reference_profile, "HY-001")
    assert_caught_on(result, "HY-001", seeded_slide(spec, "HY-001"))
    assert "TBD" in findings_for(result, "HY-001")[0].message


def test_hy001_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-001"), "HY-001")


def test_hy001_matches_whole_tokens_only(
    clean_deck: DeckModel, reference_profile: Profile
) -> None:
    """A marker that is a substring of ordinary prose must not fire.

    This is the rule's documented false-positive risk: a naive search for "TK"
    matches "stockholders", "INSERT" matches "inserted", and "ROM" matches the
    "from" that appears on three slides of the clean deck.
    """
    reference_profile.hygiene.placeholder_markers = ["TK", "INSERT", "ROM"]
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-001"), "HY-001")


def test_hy001_scans_speaker_notes(
    dirty_deck: DeckModel, reference_profile: Profile, spec: ReferenceSpec
) -> None:
    """The notes on slide 5 mention "Thursday"; a marker there must be found."""
    reference_profile.hygiene.placeholder_markers = ["Thursday"]
    result = check(dirty_deck, reference_profile, "HY-001")
    assert slide_indices(findings_for(result, "HY-001")) == {
        seeded_slide(spec, "HY-002")
    }


# --------------------------------------------------------------------------------------
# HY-002 speaker notes
# --------------------------------------------------------------------------------------


def test_hy002_catches_speaker_notes(
    dirty_deck: DeckModel, reference_profile: Profile, spec: ReferenceSpec
) -> None:
    result = check(dirty_deck, reference_profile, "HY-002")
    assert_caught_on(result, "HY-002", seeded_slide(spec, "HY-002"))


def test_hy002_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-002"), "HY-002")


def test_hy002_silent_when_notes_allowed(
    dirty_deck: DeckModel, reference_profile: Profile
) -> None:
    """The ``allow_*`` booleans are the client's answer, and must be obeyed."""
    reference_profile.hygiene.allow_speaker_notes = True
    result = check(dirty_deck, reference_profile, "HY-002")
    assert findings_for(result, "HY-002") == []
    assert [s.reason for s in result.rules_skipped if s.rule_id == "HY-002"] == [
        "the profile allows speaker notes"
    ]


# --------------------------------------------------------------------------------------
# HY-003 hidden slides
# --------------------------------------------------------------------------------------


def test_hy003_catches_hidden_slide(
    dirty_deck: DeckModel, reference_profile: Profile, spec: ReferenceSpec
) -> None:
    hidden = seeded_slide(spec, "HY-003")
    result = check(dirty_deck, reference_profile, "HY-003")
    assert_caught_on(result, "HY-003", hidden)
    assert str(hidden) in findings_for(result, "HY-003")[0].message


def test_hy003_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-003"), "HY-003")


def test_hy003_silent_when_hidden_slides_allowed(
    dirty_deck: DeckModel, reference_profile: Profile
) -> None:
    reference_profile.hygiene.allow_hidden_slides = True
    assert findings_for(check(dirty_deck, reference_profile, "HY-003"), "HY-003") == []


# --------------------------------------------------------------------------------------
# HY-004 document metadata
# --------------------------------------------------------------------------------------


def test_hy004_catches_document_metadata(
    variant_decks: dict[str, DeckModel], reference_profile: Profile
) -> None:
    result = check(variant_decks["metadata"], reference_profile, "HY-004")
    assert_caught_on(result, "HY-004", 1)
    measured = findings_for(result, "HY-004")[0].measured or ""
    for leaking in ("creator=", "lastModifiedBy=", "company="):
        assert leaking in measured


def test_hy004_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    """The generator scrubs ``core.xml`` and ``app.xml`` to empty strings.

    An empty field is not a leak, so the rule must not report it -- this is the
    test that would fail if it reported the presence of the element rather than
    the presence of a value.
    """
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-004"), "HY-004")


def test_hy004_silent_when_metadata_allowed(
    variant_decks: dict[str, DeckModel], reference_profile: Profile
) -> None:
    reference_profile.hygiene.allow_document_metadata = True
    result = check(variant_decks["metadata"], reference_profile, "HY-004")
    assert findings_for(result, "HY-004") == []


# --------------------------------------------------------------------------------------
# HY-005 comments
# --------------------------------------------------------------------------------------


def test_hy005_catches_comment(
    variant_decks: dict[str, DeckModel], reference_profile: Profile
) -> None:
    result = check(variant_decks["comments"], reference_profile, "HY-005")
    assert_caught_on(result, "HY-005", 1)
    assert "M. Director" in findings_for(result, "HY-005")[0].message


def test_hy005_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-005"), "HY-005")


def test_hy005_silent_when_comments_allowed(
    variant_decks: dict[str, DeckModel], reference_profile: Profile
) -> None:
    reference_profile.hygiene.allow_comments = True
    result = check(variant_decks["comments"], reference_profile, "HY-005")
    assert findings_for(result, "HY-005") == []


# --------------------------------------------------------------------------------------
# HY-006 empty placeholders
# --------------------------------------------------------------------------------------


def test_hy006_catches_empty_placeholder(
    dirty_deck: DeckModel, reference_profile: Profile, spec: ReferenceSpec
) -> None:
    result = check(dirty_deck, reference_profile, "HY-006")
    assert_caught_on(result, "HY-006", seeded_slide(spec, "HY-006"))
    assert findings_for(result, "HY-006")[0].bbox_pt is not None


def test_hy006_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    """Empty date, footer and slide-number placeholders are normal furniture."""
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-006"), "HY-006")


# --------------------------------------------------------------------------------------
# HY-007 external and broken relationships
# --------------------------------------------------------------------------------------


def test_hy007_catches_external_relationship(
    variant_decks: dict[str, DeckModel], reference_profile: Profile
) -> None:
    result = check(variant_decks["external_rel"], reference_profile, "HY-007")
    # The generator adds the relationship to slide 1's rels part, so the finding
    # must be mapped back onto slide 1 rather than defaulting there.
    assert_caught_on(result, "HY-007", 1)
    finding = findings_for(result, "HY-007")[0]
    assert "file:///C:/Users/analyst/Desktop/meridian_chart.png" in finding.message
    assert "image" in finding.message


def test_hy007_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    """Every internal relationship in a well-formed deck resolves to a real part.

    The root ``_rels/.rels`` is the trap here: its four relationships are valid
    and must not be reported as broken.
    """
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-007"), "HY-007")


def test_hy007_silent_when_external_relationships_allowed(
    variant_decks: dict[str, DeckModel], reference_profile: Profile
) -> None:
    reference_profile.hygiene.allow_external_relationships = True
    result = check(variant_decks["external_rel"], reference_profile, "HY-007")
    assert findings_for(result, "HY-007") == []


# --------------------------------------------------------------------------------------
# HY-008 image resolution
# --------------------------------------------------------------------------------------


def test_hy008_catches_low_resolution_image(
    dirty_deck: DeckModel, reference_profile: Profile, spec: ReferenceSpec
) -> None:
    result = check(dirty_deck, reference_profile, "HY-008")
    assert_caught_on(result, "HY-008", seeded_slide(spec, "HY-008"))
    finding = findings_for(result, "HY-008")[0]
    assert finding.shape_name == "Logo"
    assert "150" in (finding.expected or "")


def test_hy008_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    """The logo is 288px wide and is rendered at 72pt and 120pt, i.e. 288 and
    173 effective DPI. Both clear the 150 DPI floor."""
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-008"), "HY-008")


def test_hy008_honours_the_profile_floor(
    clean_deck: DeckModel, reference_profile: Profile
) -> None:
    """Raising the floor above the divider logos' DPI must start reporting them."""
    reference_profile.hygiene.min_image_dpi = 200.0
    result = check(clean_deck, reference_profile, "HY-008")
    findings = findings_for(result, "HY-008")
    assert findings, "a 200 DPI floor should catch the 173 DPI divider logos"
    assert all(f.measured and "173 DPI" in f.measured for f in findings)


# --------------------------------------------------------------------------------------
# HY-009 non-standard fonts
# --------------------------------------------------------------------------------------


def test_hy009_catches_non_standard_font(
    dirty_deck: DeckModel, reference_profile: Profile
) -> None:
    result = check(dirty_deck, reference_profile, "HY-009")
    # A deck-level finding: the seeded slide is 16, but the font is a property of
    # the deck, so the finding is reported against slide 1.
    assert_caught_on(result, "HY-009", 1)
    assert "Bodoni Sixtysix" in findings_for(result, "HY-009")[0].message


def test_hy009_silent_on_clean(clean_deck: DeckModel, reference_profile: Profile) -> None:
    """The fixture is set in Gill Sans MT and Arial, both standard faces."""
    assert_silent_on_clean(check(clean_deck, reference_profile, "HY-009"), "HY-009")


def test_hy009_accepts_a_client_declared_font(
    dirty_deck: DeckModel, reference_profile: Profile
) -> None:
    reference_profile.hygiene.extra_standard_fonts = ["bodoni sixtysix"]
    assert findings_for(check(dirty_deck, reference_profile, "HY-009"), "HY-009") == []


@pytest.mark.parametrize("typeface", ["Gill Sans MT", "Arial"])
def test_hy009_fixture_fonts_are_bundled_as_standard(typeface: str) -> None:
    """Without these two the clean deck cannot pass, so assert them directly."""
    assert typeface in STANDARD_SYSTEM_FONTS


@pytest.mark.parametrize(
    ("typeface", "expected"),
    [
        ("Segoe UI Semibold Italic", "segoe ui"),
        ("Arial Narrow", "arial"),
        ("Franklin Gothic Book", "franklin gothic"),
        ("Bodoni Sixtysix", "bodoni sixtysix"),
        ("Symbol", "symbol"),
    ],
)
def test_font_base_name_strips_only_weight_suffixes(
    typeface: str, expected: str
) -> None:
    assert _font_base_name(typeface) == expected


# --------------------------------------------------------------------------------------
# Category-wide behaviour
# --------------------------------------------------------------------------------------


def test_no_hygiene_rule_fires_on_the_clean_deck(
    clean_deck: DeckModel, reference_profile: Profile
) -> None:
    result = run_rules(clean_deck, reference_profile, include=["HY-*"])
    assert result.findings == [], (
        "hygiene rules reported on the clean deck: "
        + "; ".join(
            f"{f.rule_id} slide {f.slide_index}: {f.message}" for f in result.findings
        )
    )


def test_every_hygiene_rule_ran_on_the_clean_deck(
    clean_deck: DeckModel, reference_profile: Profile, spec: ReferenceSpec
) -> None:
    """Silence must be a pass, not a rule that never executed."""
    result = run_rules(clean_deck, reference_profile, include=["HY-*"])
    expected = {d.rule_id for d in spec.defects if d.rule_id.startswith("HY-")}
    assert set(result.rules_run) == expected
    assert result.rules_skipped == []


def test_every_hygiene_rule_states_its_false_positive_mode() -> None:
    """Section 9 requires it, and ``tieout rules`` surfaces it."""
    from tieout.rules.base import rules_in_category

    for rule_cls in rules_in_category("hygiene"):
        doc = rule_cls.__doc__ or ""
        assert "Measures:" in doc, f"{rule_cls.id} does not say what it measures"
        assert "False-positive mode:" in doc, (
            f"{rule_cls.id} does not state its false-positive mode"
        )
        assert rule_cls.summary, f"{rule_cls.id} has no summary"


@pytest.mark.parametrize(
    ("variant", "rule_id"),
    [("metadata", "HY-004"), ("comments", "HY-005"), ("external_rel", "HY-007")],
)
def test_variant_decks_are_isolated(
    variant_decks: dict[str, DeckModel],
    reference_profile: Profile,
    variant: str,
    rule_id: str,
) -> None:
    """A single-defect deck must provoke exactly one hygiene rule.

    This is what proves the variants are genuinely isolated: if the comments
    deck also reported metadata, neither test would mean anything.
    """
    result = run_rules(variant_decks[variant], reference_profile, include=["HY-*"])
    assert {f.rule_id for f in result.findings} == {rule_id}


def test_variant_rule_ids_match_the_fixture_specification(spec: ReferenceSpec) -> None:
    """Guard the mapping the tests above rely on."""
    mapping = {
        d.variant: d.rule_id
        for d in spec.defects
        if d.variant and d.rule_id.startswith("HY-")
    }
    assert mapping == {
        "metadata": "HY-004",
        "comments": "HY-005",
        "external_rel": "HY-007",
    }
