"""PLAN.md §5.2: a Low/Mid/High valuation table, read the right way up.

Read the comparables way -- the row as the scope, the column as the metric --
every case became a metric and "Implied EV/EBITDA" a scope, so no implied
multiple was recomputed and nothing recorded that it was not. A case is a
scope: the same business valued three ways, each column borrowing the
company's EBITDA where it does not state its own.
"""

from __future__ import annotations

from tests.test_real_world_figures import _blank, _chrome, _deck, _head, _table, _text
from tieout.figures import build_index
from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.rules.base import clear_caches, run_rules

_PNL = [
    ["$ in millions", "FY24A", "FY25A"],
    ["Revenue", "1,624", "1,935"],
    ["EBITDA", "389", "480"],
]


def _audit(tmp_path, valuation, *, sentence: str | None = None, beside=None):
    """The group P&L on slide 1 and ``valuation`` on slide 2, with ``beside``
    as a second table on that slide."""
    presentation = _deck()
    pnl = _blank(presentation)
    _head(pnl, "FINANCIAL PERFORMANCE", "Group Financial Performance")
    _table(pnl, 36, 120, 600, _PNL, "Group P&L")
    _chrome(pnl, 1)
    slide = _blank(presentation)
    _head(slide, "VALUATION", "Valuation Summary")
    _table(slide, 36, 120, 600, valuation, "Valuation")
    if sentence:
        _text(slide, 36, 330, 880, 20, sentence)
    if beside:
        _table(slide, 36, 380, 600, beside, "Target")
    _chrome(slide, 2)
    path = tmp_path / "deck.pptx"
    presentation.save(str(path))
    clear_caches()
    deck = load_deck(str(path))
    profile = learn_from_decks([deck], "cases").profile
    return deck, run_rules(deck, profile, include=["CO-005"])


def _declined(result) -> list[str]:
    return [r.reason for r in result.unchecked if r.rule_id == "CO-005"]


def test_a_case_with_its_own_ebitda_is_divided_by_it(tmp_path) -> None:
    """Each case's own EBITDA, not the company's on the slide beside it: 4,000
    over 400 is 10.0x, and 10.0x is stated, so only High's 9.0x (4,800 over
    480 is 10.0x) is wrong."""
    valuation = [
        ["Valuation ($m)", "Low", "High"],
        ["Enterprise value", "4,000", "4,800"],
        ["EBITDA", "400", "480"],
        ["Implied EV/EBITDA", "10.0x", "9.0x"],
    ]
    _, result = _audit(
        tmp_path, valuation, sentence="Both cases sit on LTM EBITDA of $500m."
    )
    assert [(f.slide_index, f.measured) for f in result.findings] == [(2, "9.0x")]


def test_a_case_borrowing_an_ambiguous_ebitda_is_declined(tmp_path) -> None:
    """No EBITDA in the table or on its slide, and two in the deck (FY24A and
    FY25A). Picking either would be a guess, so the multiple is declined, in
    words, and not passed in silence."""
    valuation = [
        ["Valuation ($m)", "Low", "Mid", "High"],
        ["Enterprise value", "3,840", "4,180", "4,560"],
        ["Implied EV/EBITDA", "8.0x", "8.7x", "9.5x"],
    ]
    _, result = _audit(tmp_path, valuation)
    assert not result.findings
    reasons = _declined(result)
    assert any(
        r.startswith("EV/EBITDA could not be recomputed: mid case states no")
        and "stated more than one way" in r
        for r in reasons
    ), reasons


def test_a_single_high_column_is_not_a_case_table(tmp_path) -> None:
    """One column headed "High" is as likely a 52-week high as a scenario."""
    _audit(
        tmp_path,
        [["Share price", "High"], ["Alpha Corp", "41.2"], ["Beta Industrial", "18.7"]],
    )
    index = build_index(load_deck(str(tmp_path / "deck.pptx")))
    assert not [f for f in index if f.scope.endswith(" case")]


def test_a_table_only_partly_headed_by_cases_is_not_turned(tmp_path) -> None:
    _audit(
        tmp_path,
        [["Valuation ($m)", "Low", "FY25A"], ["Enterprise value", "3,840", "4,180"]],
    )
    index = build_index(load_deck(str(tmp_path / "deck.pptx")))
    assert not [f for f in index if f.scope.endswith(" case")]


def test_a_range_of_peer_multiples_borrows_nothing(tmp_path) -> None:
    """"EV/EBITDA | Min | Max" is a range across the peers, not two valuations
    of the target. Its cases state no level of their own, so they borrow
    nothing, and the target's own EV and EBITDA on the slide are never divided
    into the peers' minimum."""
    valuation = [
        ["Trading range", "Min", "Max"],
        ["EV/EBITDA", "7.0x", "11.0x"],
    ]
    target = [["Target ($m)", "LTM"], ["Enterprise value", "4,180"], ["EBITDA", "480"]]
    _, result = _audit(tmp_path, valuation, beside=target)
    assert not result.findings, [f.message for f in result.findings]
    # Declined, in words, rather than passed in silence.
    assert any("no figure labelled enterprise value" in r for r in _declined(result))
