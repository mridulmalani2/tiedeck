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
from tieout.model.color import delta_e_76, try_parse_hex


def _cluster(hex_values: list[str], weight: float = 10.0) -> PaletteCluster:
    colours = [try_parse_hex(value) for value in hex_values]
    assert all(colour is not None for colour in colours)
    return PaletteCluster(
        rgb=colours[0],
        weight=weight,
        members=[(colour, 1) for colour in colours],
    )


def test_the_palette_tolerance_admits_its_own_clusters():
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


def test_the_tolerance_still_has_a_floor_with_one_cluster():
    tolerance, reason = derive_palette_tolerance([_cluster(["#2B2B2B"])])
    assert tolerance >= 2.0
    assert reason


def test_every_colour_the_deck_uses_is_within_tolerance_of_its_cluster():
    """Stated over the clustering itself, so the two constants cannot drift apart."""
    weighted = [
        (try_parse_hex(value), 10.0, 1)
        for value in ("#E2E5E9", "#DCE1E8", "#2B2B2B", "#C9A227", "#6B7280")
    ]
    clusters = cluster_palette(weighted)
    tolerance, _ = derive_palette_tolerance(clusters)
    for colour, _, _ in weighted:
        nearest = min(clusters, key=lambda c: delta_e_76(c.rgb, colour))
        assert delta_e_76(nearest.rgb, colour) <= tolerance


def test_review_reference_is_run_and_reported_by_learn(clean_deck):
    result = learn_from_decks([clean_deck], "guard")
    review = result.reference_review
    assert review.describe()
    # The generated reference deck is clean by construction; the point of the
    # assertion is that the review ran at all and can speak about what it found.
    assert isinstance(review.findings, list)
    assert review.clean == (not review.findings)


def test_review_reference_reports_what_a_rule_finds(clean_deck, dirty_deck):
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
