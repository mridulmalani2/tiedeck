"""What kind of claim a figure makes, and why two kinds are never one fact.

PLAN.md §0. The demo's first false positive -- "64% of revenue is recurring"
reported against "9% revenue growth" -- was not a threshold. The index carried
metric, scope, period and unit, and nothing for what kind of claim a figure
makes, so a share of revenue and a growth in it folded to one key.

Each behavioural test here is built against a deck the way §8 asks, and fails
on the index as it was before ``kind`` existed. The reading tests pin the
constructions one at a time, so a failure names the one that broke.
"""

from __future__ import annotations

import pytest

from tests.test_real_world_figures import _blank, _chrome, _deck, _head, _table, _text
from tieout.figures import Unit, build_index, label_kind, prose_kind
from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.rules.base import clear_caches, run_rules

_PNL = [
    ["$ in millions", "FY24A", "FY25A"],
    ["Revenue", "1,624", "1,770"],
    ["EBITDA", "389", "439"],
    ["EBITDA margin", "24.0%", "24.8%"],
]


def _audit(tmp_path, sentences, *, table=None, rules=("CO-001",)):
    presentation = _deck()
    slide = _blank(presentation)
    _head(slide, "FINANCIAL PERFORMANCE", "Group Financial Performance")
    _table(slide, 36, 120, 600, table or _PNL, "Group P&L")
    _chrome(slide, 1)
    for page, sentence in enumerate(sentences, start=2):
        prose = _blank(presentation)
        _head(prose, "HIGHLIGHTS", "Key Highlights")
        _text(prose, 36, 120, 880, 20, sentence)
        _chrome(prose, page)
    path = tmp_path / "deck.pptx"
    presentation.save(str(path))
    clear_caches()
    deck = load_deck(str(path))
    profile = learn_from_decks([deck], "kinds").profile
    return deck, run_rules(deck, profile, include=list(rules))


# --------------------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------------------


def test_a_share_of_a_metric_is_not_a_change_in_it(tmp_path) -> None:
    """The demo, verbatim."""
    _, result = _audit(
        tmp_path,
        ["64% of revenue is recurring in FY25A.", "9% revenue growth in FY25A."],
    )
    assert not result.findings, [f.message for f in result.findings]


def test_two_shares_of_one_base_are_not_one_fact(tmp_path) -> None:
    """"64% of revenue is recurring" and "30% of revenue is from Europe" are
    both shares of revenue, and of different parts. The part is not read, so
    neither is compared -- the demo's defect one level down."""
    _, result = _audit(
        tmp_path,
        ["64% of revenue is recurring in FY25A.", "30% of revenue is from Europe in FY25A."],
    )
    assert not result.findings, [f.message for f in result.findings]


def test_a_change_stated_as_an_amount_is_not_the_metric(tmp_path) -> None:
    """"Revenue up $146m" is a movement in revenue. Read as revenue it
    contradicted the table's 1,770 -- and left FY25A revenue stated two ways,
    so every derivation needing it refused."""
    _, result = _audit(
        tmp_path, ["Revenue up $146m in FY25A."], rules=("CO-001", "CO-004")
    )
    assert not result.findings, [f.message for f in result.findings]
    assert not [
        record.reason for record in result.unchecked if record.rule_id == "CO-004"
    ]


def test_two_changes_that_disagree_are_still_a_contradiction(tmp_path) -> None:
    """The guard: the kind must separate kinds, not silence the rule."""
    _, result = _audit(
        tmp_path, ["Revenue grew 17% in FY25A.", "15% revenue growth in FY25A."]
    )
    assert [f.slide_index for f in result.findings] == [3]


def test_a_ratio_in_prose_still_ties_to_its_table(tmp_path) -> None:
    _, result = _audit(tmp_path, ["EBITDA margin of 25.8% in FY25A."])
    assert [f.slide_index for f in result.findings] == [2]


def test_a_figure_whose_kind_is_unread_is_declined_out_loud(tmp_path) -> None:
    """PLAN.md §0's stated cost. "Churn" names no kind and a percentage is
    four, so the two statements are not compared -- and the rule says so,
    on both, rather than passing in silence."""
    table = [*_PNL, ["Churn", "4.1%", "3.8%"]]
    _, result = _audit(tmp_path, ["Churn of 4.8% in FY25A."], table=table)

    assert not result.findings
    declined = [r for r in result.unchecked if r.rule_id == "CO-001"]
    assert {r.slide_index for r in declined} == {1, 2}, [r.reason for r in declined]
    assert all("was not compared" in r.reason for r in declined)


def test_the_decks_own_tables_ground_a_kind_the_sentence_does_not_state(
    tmp_path,
) -> None:
    """The same grounding prose metrics get. The table's header states
    recurring revenue as a share of the total, so "Recurring revenue of 61%"
    -- whose own words name no kind -- is a share, and contradicts the 64%."""
    table = [
        ["$ in millions", "FY24A (% of total)", "FY25A (% of total)"],
        ["Revenue", "100%", "100%"],
        ["Recurring revenue", "62%", "64%"],
    ]
    deck, result = _audit(
        tmp_path, ["Recurring revenue of 61% in FY25A."], table=table
    )
    assert [f.slide_index for f in result.findings] == [2]
    (prose,) = [f for f in build_index(deck) if f.source == "text"]
    assert prose.kind == "share"
    assert "the deck's own tables" in prose.kind_from


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "kind"),
    [
        ("Revenue", None),
        ("EBITDA margin", "ratio"),
        ("EV/EBITDA", "ratio"),
        ("Net debt / EBITDA", "ratio"),
        ("Revenue growth", "change"),
        ("YoY %", "change"),
        ("Revenue CAGR FY23A-FY25A", "rate"),
        ("Revenue per employee", "rate"),
        ("% of revenue", "share"),
        ("Revenue mix", "share"),
        ("Number of sites", "count"),
        ("Headcount", "count"),
        ("Total addressable market", None),
    ],
)
def test_label_kinds(label: str, kind: str | None) -> None:
    assert label_kind(label) == kind


def _kind(sentence: str, figure: str, metric: str) -> str | None:
    folded = " ".join(sentence.split()).casefold()
    start = folded.index(figure.casefold())
    end = start + len(figure)
    quantity = "%" if figure.endswith("%") else "bp" if figure.endswith("bps") else None
    return prose_kind(folded, start, end, Unit(quantity=quantity), metric)[0]


@pytest.mark.parametrize(
    ("sentence", "figure", "metric", "kind"),
    [
        ("64% of revenue is recurring", "64%", "revenue", None),
        ("EBITDA at 24.8% of revenue", "24.8%", "ebitda", "share"),
        ("9% revenue growth in FY25A", "9%", "revenue", "change"),
        ("Revenue grew 17% in FY24A", "17%", "revenue", "change"),
        ("Revenue grew to $412m in FY25A", "$412m", "revenue", "level"),
        ("Revenue of $1,935m in FY25A, up from $1,352m in FY23A", "$1,935m",
         "revenue", "level"),
        ("Revenue of $1,935m in FY25A, up from $1,352m in FY23A", "$1,352m",
         "revenue", "level"),
        ("Revenue increased by $146m", "$146m", "revenue", "change"),
        ("Revenue grew at a 19.6% CAGR between FY23A and FY25A", "19.6%",
         "revenue", "rate"),
        ("EBITDA margin up 80bps", "80bps", "ebitda margin", "change"),
        ("EBITDA margin of 24.8% in FY25A", "24.8%", "ebitda margin", "ratio"),
        ("Revenue of $2.1m per site", "$2.1m", "revenue", "rate"),
        ("Churn of 4.8% in FY25A", "4.8%", "churn", None),
    ],
)
def test_prose_kinds(sentence: str, figure: str, metric: str, kind: str | None) -> None:
    assert _kind(sentence, figure, metric) == kind


def test_a_cell_whose_two_labels_name_different_kinds_is_refused(tmp_path) -> None:
    """"EBITDA margin" down the side and "YoY change" across the top is a change
    in a margin -- or a table nobody should guess about. Refused."""
    table = [
        ["$ in millions", "FY25A", "YoY change"],
        ["EBITDA margin", "24.8%", "0.8%"],
    ]
    presentation = _deck()
    slide = _blank(presentation)
    _table(slide, 36, 120, 600, table, "Margins")
    path = tmp_path / "deck.pptx"
    presentation.save(str(path))
    figures = {f.raw: f for f in build_index(load_deck(str(path)))}
    assert figures["24.8%"].kind == "ratio"
    assert figures["0.8%"].kind is None
