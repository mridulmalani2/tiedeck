"""PLAN.md §5.5: periods that were read as a different period.

§5.5 listed stub periods, a column headed "Budget" and calendar against fiscal
years as reading "as no period, which is the safe direction and costs
findings". Driven through :func:`parse_period`, three of them read as a
*different* period, which is the unsafe direction and invents findings: a
nine-month stub as the full year, a budget as the year's plain figure, and an
LTM window to one month as the LTM window to any other. Each behavioural test
here is a contradiction CO-001 reported between two figures that were never
the same fact, and fails on the previous code.
"""

from __future__ import annotations

import pytest

from tests.test_rules_consistency import _deck_with_slides, _run
from tieout.figures import parse_period


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9M 2025", "9M-2025"),
        ("9M25", "9M-2025"),
        ("6M FY25", "6M-2025"),
        ("$9m 2025", "FY2025"),
        ("2025 Budget", "FY2025B"),
        ("Budget 2025", "FY2025B"),
        ("FY25 Budget", "FY2025B"),
        ("2025 Forecast", "FY2025E"),
        ("FY25 Actual", "FY2025A"),
        ("Budget", None),
        ("CY2024", "CY2024"),
        ("CY24", "CY2024"),
        ("LTM Sep-25", "LTM-SEP-2025"),
        ("YTD to September 2025", "YTD-SEP-2025"),
    ],
)
def test_period_readings(raw: str, expected: str | None) -> None:
    assert parse_period(raw) == expected


def _two_columns(tmp_path, reference_profile, first: str, second: str):
    deck = _deck_with_slides(
        tmp_path / "periods.pptx",
        [
            [[["$ in millions", first], ["Revenue", "1,400"]]],
            [[["$ in millions", second], ["Revenue", "1,770"]]],
        ],
    )
    return _run(deck, reference_profile, "CO-001").findings


def test_a_stub_is_not_the_full_year(tmp_path, reference_profile) -> None:
    assert not _two_columns(tmp_path, reference_profile, "9M 2025", "2025")


def test_a_budget_is_not_the_year(tmp_path, reference_profile) -> None:
    assert not _two_columns(tmp_path, reference_profile, "2025 Budget", "2025")


def test_two_ltm_windows_to_different_months_are_different(
    tmp_path, reference_profile
) -> None:
    assert not _two_columns(tmp_path, reference_profile, "LTM Sep-24", "LTM Sep-25")


def test_the_same_stub_stated_twice_is_still_compared(tmp_path, reference_profile) -> None:
    """Reading a stub as its own period must not silence it -- and it gains a
    finding: "9M25" read as no period at all, so the two statements of one
    stub were never compared."""
    assert _two_columns(tmp_path, reference_profile, "9M 2025", "9M25")
