"""The detectors, one property at a time.

These are the rules that decide what leaves the machine, so they are tested as a
boundary rather than as a convenience: every assertion here is either "this must
be caught" or "this must not be mangled". The most important test in the file is
the last one — that no detector ever touches a number.
"""

from __future__ import annotations

import pytest

from tieout_review.patterns import DETECTORS, detect


def _kinds(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for span in detect(text):
        out.setdefault(span.kind, []).append(span.text)
    return out


def _all(text: str) -> list[str]:
    return [span.text for span in detect(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail j.okafor@meridian.com now", "j.okafor@meridian.com"),
        ("Deal.Team+atlas@bank.co.uk", "Deal.Team+atlas@bank.co.uk"),
        ("write to a@b.io.", "a@b.io"),
    ],
)
def test_an_email_address_is_caught_and_its_trailing_stop_is_not(text, expected):
    assert expected in _kinds(text)["email"]


@pytest.mark.parametrize(
    "text",
    [
        "see https://dealroom.example.com/atlas?id=4 for the model",
        "see www.meridiancapital.com/deals.",
        "hosted at meridiancapital.com",
    ],
)
def test_a_locator_is_caught(text):
    assert "url" in _kinds(text)


def test_a_url_does_not_keep_the_sentence_full_stop():
    """A harvested term ending in a stop matches that URL nowhere else."""
    (found,) = _kinds("see www.example.com/deals.")["url"]
    assert not found.endswith(".")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("call +44 20 7123 4567 today", "+44 20 7123 4567"),
        ("call +44 20 7123 4567.", "+44 20 7123 4567"),
        ("desk 020 7946 0102, ext 4", "020 7946 0102"),
        ("ring (0207) 946 0102", "(0207) 946 0102"),
    ],
)
def test_a_telephone_number_is_caught(text, expected):
    assert expected in _kinds(text)["phone"]


@pytest.mark.parametrize(
    "figure",
    [
        "revenue of 12 345 678 in the period",
        "revenue of 1,908,000 thousand",
        "revenue of 1.908.000 in continental format",
        "the range is 240 000 to 360 000",
    ],
)
def test_a_figure_with_separators_is_not_mistaken_for_a_telephone_number(figure):
    """The pattern anchors a domestic number on a trunk zero for exactly this.

    A deck formatted in the continental style is full of digit groups, and a
    detector that ate them would take the figures out of the payload — which is
    the only thing the payload is for.
    """
    assert "phone" not in _kinds(figure)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Meridian Capital Partners LLP agreed", "Meridian Capital Partners LLP"),
        ("acquired by Rossi & Figli S.p.A. in 2024", "Rossi & Figli S.p.A"),
        ("3M Company is a comparable", "3M Company"),
        ("Bank of the West Holdings filed", "Bank of the West Holdings"),
        ("Vantara Technologies reported", "Vantara Technologies"),
    ],
)
def test_a_company_name_with_a_suffix_is_caught_whole(text, expected):
    assert expected in _kinds(text)["company"]


def test_a_role_does_not_become_part_of_the_company_name():
    """"CFO of Meridian Capital Partners LLP" harvested whole is a false comfort:
    the term then matches the company's name nowhere else in the deck."""
    assert "Meridian Capital Partners LLP" in _kinds(
        "Prepared by the CFO of Meridian Capital Partners LLP"
    )["company"]


def test_an_article_does_not_become_part_of_the_company_name():
    assert "Meridian Group" in _kinds("The Meridian Group announced")["company"]


def test_a_company_name_does_not_run_across_a_sentence_boundary():
    """A full stop is only word-internal in an abbreviation."""
    found = _kinds("Source: analysis as at 14-September-2026. Capital in US$ millions.")
    assert "company" not in found


def test_a_company_name_does_not_run_across_a_newline():
    found = _all("London EC2M 2PF\nThe Meridian Group")
    assert "London EC2M 2PF\nThe Meridian Group" not in found
    assert "Meridian Group" in found


def test_two_companies_joined_by_and_stay_two_companies():
    found = _kinds("Meridian Group and Rossi & Figli S.p.A. are comparable")["company"]
    assert found == ["Meridian Group", "Rossi & Figli S.p.A"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Project Atlas is live", "Atlas"),
        ("Operation Falcon closed", "Falcon"),
        ("codename Blue Ridge", "Blue Ridge"),
    ],
)
def test_a_codename_is_caught_without_its_marker(text, expected):
    """Redacting "Atlas" leaves "Project [CODENAME_1]", which reads correctly."""
    assert expected in _kinds(text)["codename"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Jane Okafor, CFO", "Jane Okafor"),
        ("CFO, Jane Okafor", "Jane Okafor"),
        ("Managing Director Ade Balogun", "Ade Balogun"),
        ("Dr. Anna Weiss chairs", "Anna Weiss"),
        ("Prepared by Kwabena Osei-Bonsu", "Kwabena Osei-Bonsu"),
        ("Contact Ana de la Cruz", "Ana de la Cruz"),
    ],
)
def test_a_person_is_caught_when_a_role_or_honorific_identifies_them(text, expected):
    assert expected in _kinds(text)["person"]


def test_a_name_capture_stops_at_the_name():
    """The trigger words are matched case-insensitively and the name is not.

    With one ``re.IGNORECASE`` over the whole pattern, ``[A-Z]`` matched
    lowercase too and the capture ran on into the next preposition:
    "Kwabena Osei-Bonsu on".
    """
    assert _kinds("Contact Kwabena Osei-Bonsu on 020 7946 0102")["person"] == [
        "Kwabena Osei-Bonsu"
    ]


def test_a_ticker_needs_its_exchange_prefix():
    """A bare three-letter capital is IRR or EPS far more often than a ticker."""
    assert _kinds("listed as NYSE: MRDN")["ticker"] == ["MRDN"]
    assert "ticker" not in _kinds("IRR of 22% and EPS of 1.40")


def test_an_address_and_a_postcode_are_caught():
    found = _kinds("1 Finsbury Avenue, London EC2M 2PF")
    assert found["address"] == ["1 Finsbury Avenue"]
    assert found["postcode"] == ["EC2M 2PF"]


@pytest.mark.parametrize(
    "text",
    [
        r"source C:\Deals\Atlas\model_v12.xlsx",
        "source /Users/jokafor/Deals/atlas.xlsx",
        "source ~/Deals/atlas.xlsx",
    ],
)
def test_a_local_path_is_caught(text):
    assert "path" in _kinds(text)


def test_overlapping_detections_keep_the_longer_span():
    """Half a company name reads as cleared and is not."""
    assert "Meridian Capital Partners LLP" in _all(
        "Meridian Capital Partners LLP, NYSE: MRDN"
    )


def test_the_spans_a_detector_reports_address_the_text_it_names():
    """Offsets have to be right or the rewrite corrupts the payload."""
    text = "Prepared by Jane Okafor, CFO of Meridian Capital Partners LLP (NYSE: MRDN)."
    for span in detect(text):
        assert text[span.start : span.end] == span.text


NUMERIC_LINES = [
    "Revenue | 1,284 | 1,562 | 1,908 | 2,314 | 2,760",
    "Margin of 15.6% against 15.8% a year earlier",
    "EBITDA of US$351 million, up 33.5%",
    "(42) | (18) | 27 | 94 | 168",
    "2023A | 2024A | 2025A | 2026E | 2027E",
    "Total 2023A-2027E | 9,828 | 1,808 | n.a. | 229",
    "A range of 8.0x to 11.5x EV/EBITDA",
    "1.908 million and 1,908.0 thousand",
]


@pytest.mark.parametrize("line", NUMERIC_LINES)
def test_no_detector_ever_touches_a_figure(line):
    """The single most important property in this package.

    The bargain is that a model may see the numbers and not whose they are. A
    detector that swallowed a figure would quietly break the half of the bargain
    the tool exists to deliver, and it would look like a safe redaction.
    """
    assert detect(line) == [], detect(line)


def test_every_detector_declares_a_kind_the_redactor_can_label():
    from tieout_review.redact import _KIND_LABELS

    for detector in DETECTORS:
        assert detector.kind in _KIND_LABELS, detector.kind
