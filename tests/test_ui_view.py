"""``tieout_ui.view`` -- serialising an audit for the browser.

Focused on the §11 fixes: a document-level finding no longer masquerades as
slide 1, LO-004's second shape is reachable, and BR-006 offers the move it
already knows how to make. Everything else in this module is exercised
through ``tests/test_ui_server.py``, which drives it through the routes that
actually use it.
"""

from __future__ import annotations

from tieout.rules.base import DOCUMENT_LEVEL, Finding, run_rules
from tieout_ui.view import MOVABLE_RULES, _move_block, audit_view


def test_document_level_finding_is_not_attributed_to_slide_one(variant_decks, reference_profile):
    """HY-004/HY-005/HY-009/BR-009: a property of the file, not of slide 1.

    The audit's #39: the defect used to be listed as "slide 1", badge slide 1
    in the rail, navigate there, highlight nothing, and ✓ a slide with
    nothing wrong on it.
    """
    deck = variant_decks["metadata"]
    result = run_rules(deck, reference_profile, include=["HY-004"])
    view = audit_view(result, deck)

    assert view["document_findings"], "the metadata leak should be reported"
    slide_one = next(s for s in view["slides"] if s["index"] == 1)
    assert slide_one["findings"] == []
    assert slide_one["worst"] is None
    assert all(f["rule_id"] != "HY-004" for s in view["slides"] for f in s["findings"])


def test_document_level_finding_still_reaches_by_fix(variant_decks, reference_profile):
    """A document-level blocker with no path in "By fix" would be invisible in
    the default view -- the same silence the audit's #39 complained about, one
    layer up.
    """
    deck = variant_decks["metadata"]
    result = run_rules(deck, reference_profile, include=["HY-004"])
    view = audit_view(result, deck)

    actions = view["actions"]
    assert actions, "the metadata leak should still be one job in By fix"
    assert actions[0]["slides"] == [DOCUMENT_LEVEL]
    assert actions[0]["instances"][0]["slide"] == DOCUMENT_LEVEL


def test_lo004_also_block_names_the_second_shape(dirty_deck, reference_profile):
    """The audit's #40: only the upper shape of the overlap was outlined."""
    result = run_rules(dirty_deck, reference_profile, include=["LO-004"])
    view = audit_view(result, dirty_deck)

    finding = next(f for s in view["slides"] for f in s["findings"] if f["rule_id"] == "LO-004")
    assert finding["also"] is not None
    assert finding["also"]["uid"] != finding["move"]["uid"]
    assert finding["also"]["bbox_pt"]


def test_br006_is_in_movable_rules():
    """The audit's #6: the capability was there, only the affordance was not."""
    assert "BR-006" in MOVABLE_RULES


def test_br006_move_block_is_offered_when_a_shape_is_found(clean_deck, reference_profile):
    """BR-006 prints exact target coordinates; moving the shape it found
    should resolve it, so the page needs the same move block LO-001 gets.
    """
    box = reference_profile.brand.footer.page_number.box_pt
    box.left = box.left + 120.0
    result = run_rules(clean_deck, reference_profile, include=["BR-006"])
    view = audit_view(result, clean_deck)

    finding = next(f for s in view["slides"] for f in s["findings"] if f["rule_id"] == "BR-006")
    assert finding["move"] is not None
    assert not finding["move"]["refused"]
    assert finding["move"]["uid"] is not None

    action = next(a for a in view["actions"] if a["rule_id"] == "BR-006")
    assert action["movable"] is True


def test_br006_offers_no_move_where_no_shape_was_found(dirty_deck):
    """A slide with nothing at all in the page-number position has no shape to
    pick up -- MOVABLE_RULES membership must not manufacture one. BR-006's own
    ``_absent()`` path falls back to ``where=slide.index`` (an int) exactly
    when this happens, so this is the case ``_move_block`` already refuses.
    """
    finding = Finding(
        rule_id="BR-006",
        category="brand",
        severity="major",
        confidence="high",
        where=3,
        message="no page number on this slide",
    )
    assert _move_block(finding, dirty_deck) is None
