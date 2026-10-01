"""Evidence that a position was chosen, read once and named in every silence.

PLAN.md §0.3. Two of the demo's three false positives were layout: a decorative
shape bled off the edge on purpose, and a text box placed off the learned grid
exactly where the designer wanted it. Both were reported because a shape's
position carried no record of whether anything in the deck suggested it was
meant.

Built on the Osprey decks in :mod:`tests.corpus`: a house style, and a later
deck in it carrying those two cases beside the defects they must not swallow.
The behavioural tests fail on the rules as they were before
:func:`tieout.rules.layout.placement_intent` existed; the guards pass on both,
and are here because a rule that excuses everything also passes the first kind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.corpus import build_osprey_reference, build_osprey_target
from tests.test_real_world_constructions import _build as build_kestrel
from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.rules.base import AuditResult, clear_caches, run_rules
from tieout.rules.layout import placement_intent

LAYOUT = ["LO-001", "LO-002", "LO-003"]


@pytest.fixture(scope="module")
def osprey(tmp_path_factory: pytest.TempPathFactory):
    directory = Path(tmp_path_factory.mktemp("osprey"))
    clear_caches()
    reference = load_deck(str(build_osprey_reference(directory / "reference.pptx")))
    profile = learn_from_decks([reference], "osprey").profile
    clear_caches()
    deck = load_deck(str(build_osprey_target(directory / "target.pptx")))
    return deck, profile, run_rules(deck, profile, include=LAYOUT)


def _found(result: AuditResult, rule_id: str, slide: int) -> list[str]:
    return [
        f"{f.shape_name}: {f.message}"
        for f in result.findings
        if f.rule_id == rule_id and f.slide_index == slide
    ]


def _excused(result: AuditResult, rule_id: str, slide: int) -> list[str]:
    return [
        e.reason for e in result.excused if e.rule_id == rule_id and e.slide_index == slide
    ]


# -- the demo's cases ------------------------------------------------------------------


def test_a_bleed_on_the_cover_is_read_as_meant(osprey) -> None:
    _, _, result = osprey
    assert not _found(result, "LO-001", 1)
    assert any("title slide" in reason for reason in _excused(result, "LO-001", 1))


@pytest.mark.parametrize("slide", [2, 8])
def test_a_bleed_repeated_at_the_same_place_is_read_as_meant(osprey, slide: int) -> None:
    _, _, result = osprey
    assert not _found(result, "LO-001", slide)
    assert any(
        "the same box, at the same place" in reason
        for reason in _excused(result, "LO-001", slide)
    )


@pytest.mark.parametrize("slide", [5, 6, 7])
def test_a_box_placed_off_grid_on_every_slide_is_read_as_meant(osprey, slide: int) -> None:
    _, _, result = osprey
    assert not _found(result, "LO-003", slide)
    (reason,) = _excused(result, "LO-003", slide)
    assert "493.5pt" in reason and "slides 5, 6, 7" in reason


# -- what it must not swallow ----------------------------------------------------------


def test_a_one_off_nudge_is_still_reported(osprey) -> None:
    _, _, result = osprey
    assert any("Body nudged" in line for line in _found(result, "LO-003", 3))


def test_a_label_dragged_off_the_slide_is_still_a_blocker(osprey) -> None:
    _, _, result = osprey
    (finding,) = [
        f for f in result.findings if f.rule_id == "LO-001" and f.slide_index == 4
    ]
    assert finding.severity == "blocker"


def test_a_column_width_panel_is_not_excused_by_its_column(osprey) -> None:
    """A background panel the width of its column shares that column's edges
    with every body box on every slide. That is layout, not evidence the panel
    was meant to run off the foot of the slide."""
    _, _, result = osprey
    assert any("Stretched panel" in line for line in _found(result, "LO-001", 9))
    assert not _excused(result, "LO-001", 9)


def test_repetition_cannot_tell_a_copied_mistake_from_a_choice(osprey) -> None:
    """The stated cost, pinned so it cannot quietly change: a callout dragged
    off its column and copied to two more slides is the takeaway's twin in the
    file, and is read as meant. PLAN.md §0.3 and §9."""
    _, _, result = osprey
    assert not _found(result, "LO-003", 10)
    assert _excused(result, "LO-003", 10)


# -- the evidence itself ---------------------------------------------------------------


def test_every_kind_of_evidence_is_on_one_field(osprey, tmp_path) -> None:
    """Furniture, decorative, repeated and sparse-slide evidence on the Osprey
    target, and the data marks on the Kestrel deck, all come back through one
    call -- the point of unifying them."""
    deck, profile, _ = osprey
    kinds = {e.kind for p in placement_intent(deck, profile).values() for e in p.evidence}
    assert {"furniture", "decorative", "repeated", "sparse_slide", "aligned"} <= kinds

    clear_caches()
    kestrel = load_deck(str(build_kestrel(tmp_path / "kestrel.pptx")))
    learned = learn_from_decks([kestrel], "kestrel").profile
    marks = {
        e.kind for p in placement_intent(kestrel, learned).values() for e in p.evidence
    }
    assert "data_mark" in marks
