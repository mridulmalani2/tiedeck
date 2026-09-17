"""Invariants of the rule catalogue as a whole.

These hold today by convention. Nothing in the engine enforces them, and a
convention nobody asserts is one a future rule breaks without noticing.
"""

from __future__ import annotations

from tieout.rules.base import load_all_rules


def test_no_rule_can_block_a_send_on_less_than_high_confidence():
    """A blocker stops a deck going out. Only a finding the tool is sure of may
    do that: the arithmetic rules are provable from the file, the geometric
    ones inherit the layout model's error, and the inferential ones are
    heuristics. All three exist in the catalogue. Only the first kind may be
    a blocker, and this is where that is written down."""
    catalogue = load_all_rules()
    offenders = sorted(
        f"{rule.id} ({rule.confidence})"
        for rule in catalogue.values()
        if rule.severity == "blocker" and rule.confidence != "high"
    )
    assert not offenders, f"blocker rules below high confidence: {offenders}"


def test_every_rule_declares_its_severity_and_confidence():
    for rule in load_all_rules().values():
        assert rule.severity in ("blocker", "major", "minor", "info"), rule.id
        assert rule.confidence in ("high", "medium", "low"), rule.id
