"""PLAN.md §5.2: every place a derived rule declines, said out loud.

Each of these used to return ``None`` -- the rule's own word for "this is
fine" -- on something it had not checked at all. For a pre-send tool "I could
not verify this" and "this is fine" must never look the same, and §8 says how
to hold that: test what a rule *declined*, not only what it reported.
"""

from __future__ import annotations

from tests.test_figure_kinds import _PNL, _audit


def _declined(result, rule_id: str) -> list[str]:
    return [record.reason for record in result.unchecked if record.rule_id == rule_id]


def test_a_cagr_that_names_no_metric_is_declined(tmp_path) -> None:
    table = [["Metric", "Value"], ["CAGR FY24A-FY25A", "9.0%"]]
    _, result = _audit(tmp_path, [], table=table, rules=("CO-006",))
    assert any("names no metric" in reason for reason in _declined(result, "CO-006"))


def test_a_ratio_label_with_no_derivation_is_declined(tmp_path) -> None:
    table = [*_PNL, ["Adj. EBITDA margin", "25.0%", "25.9%"]]
    _, result = _audit(tmp_path, [], table=table, rules=("CO-004",))
    reasons = _declined(result, "CO-004")
    assert any("'adj. ebitda margin' is labelled as a ratio" in r for r in reasons), reasons
    # Once per label, not once per cell.
    assert sum("adj. ebitda margin" in r for r in reasons) == 1


def test_a_near_bridge_is_declined_and_not_reported(tmp_path) -> None:
    """The first row names an opening and the last names nothing: maybe a
    bridge, maybe not. Not reported -- a table that is not a bridge must stay
    silent -- and not passed in silence either."""
    table = [
        ["Net debt ($m)", "Value"],
        ["Opening net debt", "1,000"],
        ["Free cash flow", "(220)"],
        ["Dividends", "80"],
        ["FY25A net debt", "900"],
    ]
    _, result = _audit(tmp_path, [], table=table, rules=("CO-007",))
    assert not result.findings
    assert any("opening" in reason for reason in _declined(result, "CO-007"))


def test_a_cagr_stated_in_prose_is_recomputed(tmp_path) -> None:
    """The kind says what a label used to have to: "grew at a 21.0% CAGR"
    is a rate of revenue over the span, and 1,624 to 1,770 is 9.0%."""
    _, result = _audit(
        tmp_path,
        ["Revenue grew at a 21.0% CAGR between FY24A and FY25A."],
        rules=("CO-006",),
    )
    assert [f.slide_index for f in result.findings] == [2]


def test_a_correct_cagr_in_prose_is_silent(tmp_path) -> None:
    _, result = _audit(
        tmp_path,
        ["Revenue grew at a 9.0% CAGR between FY24A and FY25A."],
        rules=("CO-006",),
    )
    assert not result.findings
    assert not _declined(result, "CO-006")


def test_a_rate_in_prose_with_no_span_is_declined(tmp_path) -> None:
    _, result = _audit(
        tmp_path, ["Revenue grew at a 9.0% CAGR in FY25A."], rules=("CO-006",)
    )
    assert not result.findings
    assert any("rather than a span" in r for r in _declined(result, "CO-006"))
