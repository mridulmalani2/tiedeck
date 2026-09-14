"""The critical test.

Section 12: build the clean reference deck from its specification, learn from
it, and assert the derived profile recovers the parameters the deck was
literally constructed from. Then check the deck against its own derived profile
and assert zero findings.

If the learner cannot recover values the deck was built from, it is broken, and
no amount of plausible-looking YAML redeems it. This test gates everything.

The tolerances are section 12's: palette colours within Delta-E 2.0, font sets
exactly equal, logo boxes within 1pt, margins within 2pt, typography conventions
exactly equal, archetype assignment exactly equal.
"""

from __future__ import annotations

import pytest

from tieout.learn import learn_from_decks
from tieout.learn.emit import render
from tieout.model.color import delta_e_76, parse_hex
from tieout.model.furniture import font_role
from tieout.profile.loader import load
from tieout.profile.schema import Box
from tieout.rules.base import clear_caches, run_rules

#: Section 12's tolerances.
PALETTE_DELTA_E = 2.0
LOGO_BOX_PT = 1.0
MARGIN_PT = 2.0


@pytest.fixture(scope="module")
def learned(clean_deck):
    """Learn from the clean deck once, non-interactively."""
    clear_caches()
    return learn_from_decks([clean_deck], "roundtrip")


@pytest.fixture(scope="module")
def learned_profile(learned):
    return learned.profile


# --------------------------------------------------------------------------------------
# Step 2: learning asks nothing it could have worked out
# --------------------------------------------------------------------------------------


def test_learning_the_clean_deck_asks_zero_questions(learned):
    """Section 14: non-interactive learning of the clean deck asks zero questions
    and produces a working profile.

    A question the engine could have answered from evidence is a broken promise
    about onboarding costing nothing.
    """
    assert learned.question_count == 0, [
        q.question for q in learned.interview.questions
    ] + [q.question for q in learned.interview.dropped]
    assert learned.summary.startswith("No questions")


def test_the_slide_size_is_recovered_exactly(learned_profile, spec):
    assert learned_profile.slide.width_pt == pytest.approx(float(spec.width_pt))
    assert learned_profile.slide.height_pt == pytest.approx(float(spec.height_pt))


# --------------------------------------------------------------------------------------
# Step 3: the derived profile matches the specification
# --------------------------------------------------------------------------------------


def test_the_archetype_assignment_is_exactly_the_specified_one(learned_profile, spec):
    expected = {name: sorted(v) for name, v in spec.archetype_assignment.items()}
    derived = {name: sorted(v) for name, v in learned_profile.archetypes.items()}
    assert derived == expected


def test_the_palette_is_recovered_within_delta_e_two(learned_profile, spec):
    """Every declared colour must appear, and nothing extra.

    Order is not asserted: the learner sorts by weight, which is how a designer
    would rank them but is not part of the specification.
    """
    declared = [parse_hex(value) for value in spec.brand.palette_hex]
    derived = [parse_hex(value) for value in learned_profile.brand.palette_hex]

    for colour in declared:
        distance = min(delta_e_76(colour, found) for found in derived)
        assert distance <= PALETTE_DELTA_E, (
            f"{colour.hex} is not in the derived palette "
            f"{[c.hex for c in derived]} (nearest {distance:.2f} Delta-E)"
        )

    for colour in derived:
        distance = min(delta_e_76(colour, expected) for expected in declared)
        assert distance <= PALETTE_DELTA_E, (
            f"the learner invented {colour.hex}, which is {distance:.2f} Delta-E "
            f"from anything the deck declares"
        )
    assert len(derived) == len(declared)


def test_the_palette_tolerance_cannot_merge_two_brand_colours(learned_profile):
    """Section 8.3's guarantee. If the tolerance exceeded half the closest pair's
    separation, a shape set in one brand colour could pass as another."""
    colours = [parse_hex(value) for value in learned_profile.brand.palette_hex]
    closest = min(
        delta_e_76(a, b)
        for index, a in enumerate(colours)
        for b in colours[index + 1 :]
    )
    assert learned_profile.brand.palette_tolerance_delta_e <= closest / 2.0
    assert learned_profile.brand.palette_tolerance_delta_e >= 2.0


def test_nothing_was_discarded_from_a_self_consistent_deck(learned_profile):
    """The clean deck uses exactly its declared palette, so the "colours used
    once, possibly errors" list must be empty. A non-empty list here would mean
    the fixture is not as clean as it claims."""
    assert learned_profile.brand.palette_discarded == []


def test_the_font_set_is_exactly_the_specified_one(learned_profile, spec):
    assert set(learned_profile.brand.fonts.allowed) == set(spec.brand.fonts_allowed)


def test_every_role_permits_exactly_the_sizes_the_deck_uses(
    learned_profile, clean_deck
):
    """Measured against the deck rather than against a hardcoded list.

    Hardcoding the expected bands would duplicate the role-inference rules in the
    test, so the test would keep passing if both drifted together. Reading the
    sizes back off the deck means the assertion is genuinely a round trip.
    """
    from tieout.model.furniture import detect_furniture

    furniture = detect_furniture(clean_deck, learned_profile)
    observed: dict[str, set[float]] = {}
    for slide in clean_deck.slides:
        for shape in slide.leaf_shapes():
            role = font_role(slide, shape, furniture=furniture)
            for paragraph in shape.all_paragraphs:
                for run in paragraph.runs:
                    if run.text.strip() and run.font.size_pt is not None:
                        observed.setdefault(role, set()).add(run.font.size_pt)

    for role, sizes in sorted(observed.items()):
        band = learned_profile.brand.fonts.roles.get(role)
        if band is None:
            assert any(
                entry.key == f"brand.fonts.roles.{role}"
                for entry in learned_profile.not_learned
            ), f"role {role!r} was neither learned nor recorded as not learned"
            continue
        for size in sorted(sizes):
            assert band.permits(size), (
                f"role {role!r} band {band.describe()} rejects {size}pt, which the "
                f"reference deck actually uses"
            )


def test_the_logo_is_identified_and_its_boxes_recovered_within_one_point(
    learned_profile, spec, logo_sha1
):
    logo = learned_profile.brand.logo
    assert logo is not None
    assert logo_sha1 in logo.image_sha1

    for slide_spec in spec.slides:
        archetype = slide_spec.archetype
        if not slide_spec.has_logo:
            assert logo.is_exempt(archetype) or not logo.is_known(archetype), (
                f"{archetype} carries no logo in the deck but the profile expects one"
            )
            continue
        expected = (
            spec.brand.logo_divider_box_pt
            if slide_spec.logo_centred
            else spec.brand.logo_box_pt
        )
        box = logo.box_for(archetype)
        assert isinstance(box, Box), f"no logo box learned for {archetype}"
        for name, derived, declared in (
            ("left", box.left, expected[0]),
            ("top", box.top, expected[1]),
            ("width", box.width, expected[2]),
            ("height", box.height, expected[3]),
        ):
            assert abs(derived - declared) <= LOGO_BOX_PT, (
                f"{archetype} logo {name}: derived {derived}, declared {declared}"
            )


def test_archetypes_with_no_logo_are_exempt_not_merely_unchecked(learned_profile):
    """"Absent by design" and "no evidence either way" must not produce the same
    finding, so the distinction has to survive into the profile."""
    logo = learned_profile.brand.logo
    assert logo is not None
    assert logo.is_exempt("title")
    assert logo.is_exempt("disclaimer")


def test_the_page_number_convention_is_recovered(learned_profile, spec):
    page_number = learned_profile.brand.footer.page_number
    assert page_number is not None
    assert page_number.regex == r"^\d+$", "the deck uses bare digits"
    assert page_number.must_ascend

    expected = {s.archetype for s in spec.slides if s.has_page_number}
    assert set(page_number.required_on) <= expected
    assert "content" in page_number.required_on

    box = page_number.box_pt
    assert box is not None
    declared = spec.brand.page_number_box_pt
    assert abs(box.left - declared[0]) <= LOGO_BOX_PT
    assert abs(box.top - declared[1]) <= LOGO_BOX_PT


def test_the_confidentiality_line_is_recovered_and_recognised(learned_profile, spec):
    entries = learned_profile.brand.footer.boilerplate
    assert entries, "the deck carries a confidentiality line on every slide"
    match = next(
        (e for e in entries if e.text == spec.brand.confidentiality_text), None
    )
    assert match is not None, [e.text for e in entries]
    assert match.is_confidentiality, "it should be recognised as a marking, not a footer"
    assert "content" in match.required_on


def test_every_typography_convention_is_recovered_exactly(learned_profile, spec):
    derived = learned_profile.typography
    declared = spec.typography
    assert derived.quotes == declared.quotes
    assert derived.title_case == declared.title_case
    assert derived.bullet_terminal_punctuation == declared.bullet_terminal_punctuation
    assert derived.thousands_separator == declared.thousands_separator
    assert derived.negative_style == declared.negative_style
    assert derived.decimal_places_by_column == declared.decimal_places_by_column
    assert derived.date_format == declared.date_format


def test_the_currency_pattern_matches_the_deck_and_rejects_the_alternative(
    learned_profile, spec
):
    import re

    pattern = learned_profile.typography.currency_pattern
    assert pattern is not None
    assert re.match(pattern, f"{spec.typography.currency_prefix}8,420")
    assert not re.match(pattern, "USD 8,420"), (
        "the deck never writes USD, so the learned pattern must not admit it"
    )


def test_the_advisor_mark_becomes_a_canon_term(learned_profile, spec):
    """A recurring proper noun is worth a canon entry even with no observed
    variants: it is what gives TY-005 something to check a future deck against."""
    assert spec.advisor_mark in learned_profile.typography.canon_terms


def test_the_safe_margins_are_recovered_within_two_points(learned_profile):
    margins = learned_profile.layout.safe_margin_pt
    assert "content" in margins
    content = margins["content"]
    from tieout.fixtures.spec import MARGIN_LEFT_PT, MARGIN_RIGHT_PT

    assert abs(content.left - MARGIN_LEFT_PT) <= MARGIN_PT
    assert abs(content.right - MARGIN_RIGHT_PT) <= MARGIN_PT


def test_the_grid_recovers_the_columns_the_deck_is_built_on(learned_profile):
    from tieout.fixtures.spec import (
        CONTENT_LEFT_PT,
        CONTENT_RIGHT_PT,
    )

    columns = learned_profile.layout.grid.columns_pt
    assert columns, "the deck is built on a grid; the learner must find it"
    for edge in (CONTENT_LEFT_PT, CONTENT_RIGHT_PT):
        assert any(abs(edge - line) <= 2.0 for line in columns), (
            f"{edge}pt is used by almost every shape but is not a learned grid line"
        )


def test_thin_evidence_is_recorded_rather_than_guessed_at(learned_profile):
    """Section 8.5's own example: the chart label role cannot be derived from two
    charts, and the honest outcome is to say so."""
    keys = learned_profile.not_learned_keys()
    assert "brand.fonts.roles.chart_label" in keys
    assert "layout.gutter_pt" in keys
    for entry in learned_profile.not_learned:
        assert entry.reason, f"{entry.key} is not learned but gives no reason"


def test_every_derived_value_carries_its_provenance(learned_profile):
    """Section 14: every derived value in an emitted profile carries provenance.

    Checked as coverage of the sections that were actually derived, because a
    value with no traceable evidence should not have been emitted at all.
    """
    required = [
        "brand.fonts.allowed",
        "brand.palette_hex",
        "brand.palette_tolerance_delta_e",
        "brand.logo",
        "brand.footer.page_number",
        "layout.grid.columns_pt",
        "typography.quotes",
        "typography.date_format",
        "hygiene",
    ]
    missing = [path for path in required if not learned_profile.provenance_for(path)]
    assert not missing, f"no provenance recorded for {missing}"


def test_the_provenance_cites_real_counts(learned_profile):
    """Section 16: every number must be traceable to an observation count."""
    logo_note = learned_profile.provenance_for("brand.logo")
    assert logo_note is not None
    assert "23 of 26" in logo_note, logo_note


# --------------------------------------------------------------------------------------
# Step 4: the deck passes its own profile
# --------------------------------------------------------------------------------------


def test_the_clean_deck_produces_zero_findings_against_its_own_profile(
    clean_deck, learned_profile
):
    """The heart of the whole exercise.

    Learn from a deck, then check that deck: anything reported is the tool
    disagreeing with the very material it was taught from.
    """
    clear_caches()
    result = run_rules(clean_deck, learned_profile)
    assert result.findings == [], "\n".join(
        f"slide {f.slide_index} {f.rule_id} [{f.severity}] "
        f"{f.shape_name or '-'}: {f.message}"
        for f in result.findings
    )


def test_all_36_rules_are_accounted_for(clean_deck, learned_profile):
    """Every rule must run or say why not. A rule that does neither is a silent
    gap in the audit."""
    from tieout.rules.base import load_all_rules

    clear_caches()
    result = run_rules(clean_deck, learned_profile)
    registry = load_all_rules()
    assert len(registry) == 36

    accounted = set(result.rules_run) | {s.rule_id for s in result.rules_skipped}
    assert accounted == set(registry), (
        f"unaccounted for: {sorted(set(registry) - accounted)}"
    )


def test_the_only_rules_not_run_are_the_ones_that_ship_disabled(
    clean_deck, learned_profile
):
    clear_caches()
    result = run_rules(clean_deck, learned_profile)
    skipped = {s.rule_id for s in result.rules_skipped}
    assert skipped == {"LO-006", "TY-009"}, skipped


def test_the_deck_still_passes_with_the_off_by_default_rules_enabled(
    clean_deck, learned_profile
):
    """LO-006 and TY-009 ship off because one is approximate and the other needs
    a client dictionary. Neither should fire on the reference deck when switched
    on -- the client dictionary is seeded from the reference deck's own
    vocabulary, so its invented company names are not reported as misspellings.
    A spelling finding here means either the bundled dictionary is inadequate for
    banking prose or the vocabulary seeding has regressed."""
    profile = learned_profile.model_copy(deep=True)
    profile.rules.disabled = []
    profile.rules.enabled = ["LO-006", "TY-009"]
    clear_caches()
    result = run_rules(clean_deck, profile)
    assert result.findings == [], "\n".join(
        f"slide {f.slide_index} {f.rule_id}: {f.message}" for f in result.findings
    )


# --------------------------------------------------------------------------------------
# The emitted document
# --------------------------------------------------------------------------------------


def test_the_profile_survives_a_write_and_read_unchanged(learned_profile, tmp_path):
    """A profile is a document a user edits, so the round trip through YAML has to
    preserve every value and every provenance note."""
    path = tmp_path / "roundtrip.yaml"
    path.write_text(render(learned_profile), encoding="utf-8")
    reloaded = load(path)

    assert reloaded.brand.palette_hex == learned_profile.brand.palette_hex
    assert reloaded.brand.fonts.allowed == learned_profile.brand.fonts.allowed
    assert reloaded.archetypes == learned_profile.archetypes
    assert reloaded.typography.model_dump() == learned_profile.typography.model_dump()
    assert reloaded.layout.model_dump() == learned_profile.layout.model_dump()
    assert reloaded.provenance == learned_profile.provenance
    assert reloaded.not_learned_keys() == learned_profile.not_learned_keys()


def test_a_reloaded_profile_still_produces_zero_findings(
    clean_deck, learned_profile, tmp_path
):
    """The path a real user takes: learn, close the terminal, come back and check.

    A value lost in serialisation would show up here as a finding on a deck that
    passed a moment earlier.
    """
    path = tmp_path / "reloaded.yaml"
    path.write_text(render(learned_profile), encoding="utf-8")
    clear_caches()
    result = run_rules(clean_deck, load(path))
    assert result.findings == [], "\n".join(
        f"slide {f.slide_index} {f.rule_id}: {f.message}" for f in result.findings
    )


def test_the_emitted_yaml_explains_itself(learned_profile):
    text = render(learned_profile)
    assert text.startswith("# Generated by tieout learn on ")
    assert "why:" in text, "derived values must carry their evidence"
    assert "reference_clean.pptx" in text
    assert "No questions" in text


def test_learning_is_deterministic(clean_deck):
    """Same deck, same profile, byte for byte, apart from the date stamp.

    Two runs that disagree would mean the tool's findings depend on dictionary
    iteration order, which is not something a user can be asked to live with.
    """
    clear_caches()
    first = learn_from_decks([clean_deck], "determinism").profile
    clear_caches()
    second = learn_from_decks([clean_deck], "determinism").profile
    assert first.model_dump() == second.model_dump()
    assert first.provenance == second.provenance
