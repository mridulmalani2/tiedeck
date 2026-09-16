"""The self-check `learn` runs, and the three coherence bugs it was built to expose.

A profile derived from a deck and then run against that same deck should find
nothing. That is the tool's acceptance criterion, and until now it was only ever
asserted against the deck TieOut generates for itself -- a deck built to fit
TieOut's own assumptions, which is the one deck that cannot test them.

These tests hold the *derivers* to the invariant directly: each takes a
construction a real deck contains, derives a profile from it, and asserts the
profile admits what it was derived from.
"""

from __future__ import annotations

from tieout.learn import learn_from_decks, review_reference
from tieout.learn.derive_brand import (
    PaletteCluster,
    cluster_palette,
    derive_palette_tolerance,
)
from tieout.model.color import Rgb, delta_e_76, try_parse_hex
from tieout.model.deck import DeckModel


def _rgb(value: str) -> Rgb:
    colour = try_parse_hex(value)
    assert colour is not None, f"{value} is not an sRGB triplet"
    return colour


def _cluster(hex_values: list[str], weight: float = 10.0) -> PaletteCluster:
    colours = [_rgb(value) for value in hex_values]
    return PaletteCluster(
        rgb=colours[0],
        weight=weight,
        members=[(colour, 1) for colour in colours],
    )


def test_the_palette_tolerance_admits_its_own_clusters() -> None:
    """The bug this was written for.

    Clustering gathers colours within 3 Delta-E of each other; the tolerance was
    derived from the distance *between* clusters and came out at 2. A member on
    the rim of its own cluster then failed a check against the palette it is part
    of -- ten of the seventeen colour findings a clean deck produced against its
    own profile.
    """
    clusters = [
        _cluster(["#E2E5E9", "#DCE1E8"]),   # 2.3 Delta-E apart
        _cluster(["#2B2B2B"]),
        _cluster(["#C9A227"]),
    ]
    tolerance, reason = derive_palette_tolerance(clusters)

    for cluster in clusters:
        for member, _ in cluster.members:
            assert delta_e_76(cluster.rgb, member) <= tolerance, (
                f"{member.hex} is in {cluster.hex}'s cluster but outside the "
                f"tolerance {tolerance} that BR-004 will measure it against"
            )
    assert reason


def test_the_tolerance_still_has_a_floor_with_one_cluster() -> None:
    tolerance, reason = derive_palette_tolerance([_cluster(["#2B2B2B"])])
    assert tolerance >= 2.0
    assert reason


def test_every_colour_the_deck_uses_is_within_tolerance_of_its_cluster() -> None:
    """Stated over the clustering itself, so the two constants cannot drift apart."""
    weighted = [
        (_rgb(value), 10.0, 1)
        for value in ("#E2E5E9", "#DCE1E8", "#2B2B2B", "#C9A227", "#6B7280")
    ]
    clusters = cluster_palette(weighted)
    tolerance, _ = derive_palette_tolerance(clusters)
    for colour, _, _ in weighted:
        nearest = min(clusters, key=lambda c: delta_e_76(c.rgb, colour))
        assert delta_e_76(nearest.rgb, colour) <= tolerance


def test_review_reference_is_run_and_reported_by_learn(clean_deck: DeckModel) -> None:
    result = learn_from_decks([clean_deck], "guard")
    review = result.reference_review
    assert review.describe()
    # The generated reference deck is clean by construction; the point of the
    # assertion is that the review ran at all and can speak about what it found.
    assert isinstance(review.findings, list)
    assert review.clean == (not review.findings)


def test_review_reference_reports_what_a_rule_finds(
    clean_deck: DeckModel, dirty_deck: DeckModel
) -> None:
    """A profile learned from one deck, reviewed against a deck with defects.

    Not the normal call -- `learn` reviews the deck it learned from -- but it is
    the only way to assert the review reports rather than swallows, without
    seeding a defect into the clean deck.
    """
    profile = learn_from_decks([clean_deck], "guard").profile
    review = review_reference([dirty_deck], profile)
    assert not review.clean
    assert review.findings
    assert "finding(s) remain on the reference deck itself" in review.describe()
    assert review.blocking_count >= 1


# --------------------------------------------------------------------------------------
# A rule that crashed has not passed
# --------------------------------------------------------------------------------------


def test_a_rule_that_raises_is_marked_failed_not_merely_skipped(
    clean_deck: DeckModel,
) -> None:
    """The distinction this test exists for cost a real diagnosis.

    A typo in a layout helper made LO-003 raise on every deck. The engine caught
    it, filed it in ``rules_skipped`` beside the rules that decline for want of a
    learned expectation, and the report said "0 findings from 37 rules" -- one
    fewer than the run before, which is the only trace a reader had that a rule
    had stopped checking anything at all.
    """
    from tieout.profile.schema import Profile
    from tieout.rules.base import (
        REGISTRY,
        Finding,
        Rule,
        load_all_rules,
        register,
        run_rules,
    )

    load_all_rules()
    profile = learn_from_decks([clean_deck], "guard").profile

    @register
    class Exploding(Rule):
        id = "ZZ-999"
        category = "layout"
        summary = "raises"

        def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
            raise RuntimeError("boom")

    try:
        result = run_rules(clean_deck, profile, include=["ZZ-999"])
    finally:
        REGISTRY.pop("ZZ-999", None)

    entries = [entry for entry in result.rules_skipped if entry.rule_id == "ZZ-999"]
    assert entries, "the crashed rule was not recorded at all"
    assert entries[0].failed
    assert "boom" in entries[0].reason
    assert "ZZ-999" not in result.rules_run


def test_a_declined_rule_is_not_marked_failed(clean_deck: DeckModel) -> None:
    """Declining for want of an expectation is the rule working, not failing."""
    from tieout.rules.base import run_rules

    profile = learn_from_decks([clean_deck], "guard").profile
    profile.brand.logo = None
    result = run_rules(clean_deck, profile, include=["BR-001"])
    entries = [entry for entry in result.rules_skipped if entry.rule_id == "BR-001"]
    assert entries
    assert not entries[0].failed


def test_a_review_with_a_failed_rule_is_not_clean() -> None:
    """Zero findings from a rule that never ran is not evidence of anything."""
    from tieout.learn import ReferenceReview

    review = ReferenceReview(failed_rules=[("LO-003", "raised NameError: nope")])
    assert not review.clean
    assert "failed to run" in review.describe()
    assert "LO-003" in review.describe()
