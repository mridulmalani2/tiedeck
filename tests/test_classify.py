"""Observation classification, on hand-built observation sets.

Section 8.2's table in isolation: given these observations, is there a rule
here, and how confident should it be? Hand-built rather than deck-derived
because each class needs to be provoked exactly, and a real deck exercises one
or two of them at a time.

The case that matters most is VARIABLE. Section 16 says the engine must never
invent a rule it cannot justify, and silence is correct -- so "no rule" has to be
a reachable, tested outcome rather than a fallback nobody exercises.
"""

from __future__ import annotations

import pytest

from tieout.cluster import (
    categorical_dominance,
    cluster_values,
    derive_tolerance,
    dominance,
    floor_to,
    percentile,
)
from tieout.learn.classify import (
    DOMINANT_SHARE,
    MIN_SUPPORT_ARCHETYPE,
    MIN_SUPPORT_DECK,
    ObservationClass,
    classify_categorical,
    classify_numeric,
    explain_by_archetype,
    min_support_for,
)
from tieout.learn.observe import DECK, Observation, archetype_scope


def _numeric(values, key="logo.left", scope=DECK, weight=1.0):
    return [
        Observation(key, scope, value, index + 1, weight)
        for index, value in enumerate(values)
    ]


def _categorical(values, key="quotes", scope=DECK, weight=1.0):
    return [
        Observation(key, scope, value, index + 1, weight)
        for index, value in enumerate(values)
    ]


# --------------------------------------------------------------------------------------
# INVARIANT
# --------------------------------------------------------------------------------------


def test_one_value_with_enough_support_is_invariant():
    result = classify_numeric("logo.left", DECK, _numeric([852.0] * 12))
    assert result.observation_class is ObservationClass.INVARIANT
    assert result.value == 852.0
    assert result.confidence == "high"
    assert not result.outliers
    assert "invariant" in result.provenance(total_slides=26)


def test_values_within_the_tolerance_are_one_value():
    """A logo placed by hand at 871.9 and 872.1 is at one position. Treating those
    as two would make every real deck multimodal and nothing would ever be
    learned."""
    result = classify_numeric(
        "logo.left", DECK, _numeric([872.0, 871.9, 872.1, 872.0]), tolerance=2.0
    )
    assert result.observation_class is ObservationClass.INVARIANT
    assert result.spread == pytest.approx(0.2, abs=1e-6)


def test_font_sizes_cluster_at_zero_tolerance():
    """10pt and 11pt are different sizes, not two readings of one."""
    result = classify_numeric(
        "font.size", DECK, _numeric([10.0, 11.0, 10.0, 11.0]), tolerance=0.0
    )
    assert result.observation_class is not ObservationClass.INVARIANT


def test_a_categorical_invariant():
    result = classify_categorical("quotes", DECK, _categorical(["curly"] * 8))
    assert result.observation_class is ObservationClass.INVARIANT
    assert result.value == "curly"
    assert result.confidence == "high"


# --------------------------------------------------------------------------------------
# DOMINANT
# --------------------------------------------------------------------------------------


def test_a_dominant_value_names_its_outliers():
    """The interview's highest-value question comes from exactly this: 23 slides
    agree and two do not, and only the user knows whether those two are
    exceptions or errors."""
    observations = [
        *_numeric([852.0] * 23),
        Observation("logo.left", DECK, 840.0, 7),
        Observation("logo.left", DECK, 840.0, 19),
    ]
    result = classify_numeric("logo.left", DECK, observations)
    assert result.observation_class is ObservationClass.DOMINANT
    assert result.value == 852.0
    assert result.confidence == "medium", "a dominant rule is not a certain one"
    assert {o.slide_index for o in result.outliers} == {7, 19}
    assert result.top_share >= DOMINANT_SHARE


def test_the_dominance_threshold_is_the_specified_eighty_five_per_cent():
    just_under = _numeric([852.0] * 8) + _numeric([840.0] * 2)
    assert classify_numeric("logo.left", DECK, just_under).observation_class is not (
        ObservationClass.DOMINANT
    )
    just_over = _numeric([852.0] * 18) + _numeric([840.0] * 2)
    assert (
        classify_numeric("logo.left", DECK, just_over).observation_class
        is ObservationClass.DOMINANT
    )


def test_dominance_is_weighted_not_counted():
    """A deck with three hundred curly apostrophes and two straight ones has a
    convention plus two defects. Counting observations rather than weighting them
    would call that a tie between two shapes."""
    observations = [
        Observation("quotes", DECK, "curly", 1, 300.0),
        Observation("quotes", DECK, "straight", 2, 2.0),
        Observation("quotes", DECK, "straight", 3, 1.0),
    ]
    result = classify_categorical("quotes", DECK, observations)
    assert result.observation_class is ObservationClass.DOMINANT
    assert result.value == "curly"


def test_the_emitted_value_is_the_modal_one_not_the_mean():
    """A mean of 852 and 840 is 846, a position nothing on the deck occupies."""
    observations = _numeric([852.0] * 20) + _numeric([840.0] * 2)
    result = classify_numeric("logo.left", DECK, observations)
    assert result.value == 852.0


# --------------------------------------------------------------------------------------
# MULTIMODAL
# --------------------------------------------------------------------------------------


def test_two_real_modes_are_multimodal_rather_than_a_winner():
    observations = _numeric([852.0] * 18) + _numeric([420.0] * 5)
    result = classify_numeric("logo.left", DECK, observations)
    assert result.observation_class is ObservationClass.MULTIMODAL
    assert sorted(result.allowed) == [420.0, 852.0]
    assert result.confidence == "medium"


def test_a_mode_below_fifteen_per_cent_does_not_count_as_one():
    observations = _numeric([852.0] * 40) + _numeric([420.0] * 3)
    result = classify_numeric("logo.left", DECK, observations)
    assert result.observation_class is ObservationClass.DOMINANT


def test_a_multimodal_key_explained_by_archetype_becomes_per_archetype_rules():
    """The whole point of the archetype machinery: "the logo has two positions,
    no rule" becomes two usable rules."""
    content = classify_numeric(
        "logo.left",
        archetype_scope("content"),
        _numeric([852.0] * 12, scope=archetype_scope("content")),
    )
    divider = classify_numeric(
        "logo.left",
        archetype_scope("section_divider"),
        _numeric([420.0] * 4, scope=archetype_scope("section_divider")),
    )
    assert explain_by_archetype(
        "logo.left", {"content": content, "section_divider": divider}
    )


def test_archetypes_that_agree_do_not_explain_a_multimodal_key():
    a = classify_numeric("logo.left", archetype_scope("content"), _numeric([852.0] * 5))
    b = classify_numeric("logo.left", archetype_scope("table_heavy"), _numeric([852.0] * 5))
    assert not explain_by_archetype("logo.left", {"content": a, "table_heavy": b})


def test_a_single_archetype_cannot_explain_anything():
    only = classify_numeric("logo.left", archetype_scope("content"), _numeric([852.0] * 5))
    assert not explain_by_archetype("logo.left", {"content": only})


# --------------------------------------------------------------------------------------
# VARIABLE
# --------------------------------------------------------------------------------------


def test_thin_evidence_produces_no_rule_and_says_why():
    """Section 8.5's own worked example: two charts is below min_support of three,
    so the chart rule is recorded as not learned rather than derived from two
    observations."""
    result = classify_numeric("chart.label", DECK, _numeric([9.0, 9.0]))
    assert result.observation_class is ObservationClass.VARIABLE
    assert not result.learned
    assert "below min_support of 3" in result.reason
    assert result.value is None


def test_structureless_observations_produce_no_rule():
    result = classify_numeric(
        "gutter", DECK, _numeric([8.0, 12.0, 17.0, 21.0, 26.0, 30.0, 34.0, 9.0, 14.0])
    )
    assert result.observation_class is ObservationClass.VARIABLE
    assert "no consistent value" in result.reason
    assert "entropy" in result.reason


def test_high_entropy_prevents_a_spurious_multimodal_rule():
    """Several equally weighted values is noise, not a set of conventions,
    whatever the top share happens to be."""
    result = classify_categorical(
        "date_format", DECK, _categorical(["a", "b", "c", "d", "e", "f", "g", "h"])
    )
    assert result.observation_class is ObservationClass.VARIABLE
    assert result.entropy > 1.6


def test_no_observations_at_all():
    result = classify_numeric("nothing", DECK, [])
    assert result.observation_class is ObservationClass.VARIABLE
    assert result.reason == "no observations"


# --------------------------------------------------------------------------------------
# Support thresholds and tolerances
# --------------------------------------------------------------------------------------


def test_min_support_varies_by_scope_as_specified():
    assert min_support_for(DECK) == MIN_SUPPORT_DECK == 3
    assert min_support_for(archetype_scope("content")) == MIN_SUPPORT_ARCHETYPE == 2
    assert min_support_for("table:3:9:col:2") == MIN_SUPPORT_ARCHETYPE
    assert min_support_for(DECK, structural=True) == 1


def test_a_structural_singular_needs_only_one_observation():
    """Slide dimensions are singular by construction; demanding three would leave
    the canvas size unlearnable."""
    result = classify_numeric(
        "slide.width", DECK, _numeric([960.0]), structural=True
    )
    assert result.observation_class is ObservationClass.INVARIANT


def test_tolerance_is_derived_from_the_observed_spread():
    """A client with sloppy but acceptable placement gets a looser rule
    automatically, rather than forty false positives."""
    assert derive_tolerance(0.0, floor=2.0) == 2.0
    assert derive_tolerance(4.0, floor=2.0) == 6.0
    assert derive_tolerance(100.0, floor=2.0, ceiling=10.0) == 10.0


def test_clustering_does_not_chain_through_a_gradual_run():
    """Single linkage on nearest neighbour would collapse 0, 2, 4, 6, 8 into one
    cluster spanning 8pt, which would then be emitted as a grid line nothing
    sits on."""
    clusters = cluster_values([0.0, 2.0, 4.0, 6.0, 8.0], tolerance=2.0)
    assert len(clusters) > 1


def test_clustering_is_stable_under_input_order():
    values = [872.0, 840.0, 872.0, 871.5, 840.5]
    forward = [c.mode for c in cluster_values(values, 2.0)]
    backward = [c.mode for c in cluster_values(list(reversed(values)), 2.0)]
    assert forward == backward


def test_weights_must_match_values():
    with pytest.raises(ValueError, match="same length"):
        cluster_values([1.0, 2.0], tolerance=1.0, weights=[1.0])


def test_percentile_and_flooring():
    assert percentile([], 0.5) is None
    assert percentile([42.0], 0.05) == 42.0
    assert percentile([32.0, 32.0, 36.0, 40.0, 120.0], 0.0) == 32.0
    assert percentile([0.0, 100.0], 0.5) == pytest.approx(50.0)
    assert floor_to(33.9, 2.0) == 32.0
    assert floor_to(36.0, 2.0) == 36.0
    assert floor_to(5.0, 0.0) == 5.0


def test_empty_dominance_is_not_an_error():
    result = dominance([], tolerance=1.0)
    assert result.top is None
    assert result.top_share == 0.0
    assert result.entropy == 0.0
    empty = categorical_dominance([])
    assert empty.top is None
    assert empty.distinct == 0
