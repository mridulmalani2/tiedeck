"""The redaction boundary.

This is the file that decides whether the package is safe to use. It is longer
than the module it tests, deliberately: the interesting failures here are not
crashes but silent under-redaction, which looks exactly like success.
"""

from __future__ import annotations

import pytest

from tieout_review.patterns import Detector
from tieout_review.redact import (
    FINANCE_VOCABULARY,
    PLACEHOLDER_RE,
    Redacted,
    Redactor,
    TermSource,
)


def _redact(corpus: str, **terms: str) -> Redacted:
    mapping = {term: TermSource(kind, "blocklist") for term, kind in terms.items()}
    return Redactor(mapping).apply(corpus)


# --------------------------------------------------------------------------- #
# Literal terms
# --------------------------------------------------------------------------- #


def test_a_listed_term_is_replaced_everywhere():
    out = _redact("Vantara grew. Vantara then fell. VANTARA held.", Vantara="company")
    assert "Vantara" not in out.text
    assert "VANTARA" not in out.text
    assert out.text.count("[COMPANY_1]") == 3
    assert out.redactions[0].occurrences == 3


def test_a_term_is_matched_across_a_line_break():
    """A company name wrapping inside a text box arrives with a newline in it.

    Matching the literal string would miss it, and the miss would be invisible.
    """
    out = _redact("Meridian\nCapital reported", **{"Meridian Capital": "company"})
    assert "Meridian" not in out.text
    assert "Capital" not in out.text


def test_a_term_is_matched_across_a_non_breaking_space():
    out = _redact("Meridian Capital reported", **{"Meridian Capital": "company"})
    assert "Meridian" not in out.text


def test_a_term_is_not_matched_inside_a_longer_word():
    """Word boundaries, or a three-letter blocklist entry redacts half the deck."""
    out = _redact("the Atlas atlas-like Atlantic Atlases", Atlas="codename")
    assert "Atlantic" in out.text
    assert "atlas-like" not in out.text  # a hyphen is a word boundary; this is caught


def test_a_term_ending_in_punctuation_still_matches():
    out = _redact("filed by Rossi S.p.A. yesterday", **{"Rossi S.p.A.": "company"})
    assert "Rossi" not in out.text


def test_the_same_term_gets_the_same_placeholder_and_different_terms_do_not():
    out = _redact(
        "Vantara and Calderwood and Vantara", Vantara="company", Calderwood="company"
    )
    placeholders = set(PLACEHOLDER_RE.findall(out.text))
    assert len(placeholders) == 2


def test_placeholders_are_numbered_by_first_appearance():
    """Stable numbering makes two runs of the same deck comparable."""
    out = _redact("Bravo then Alpha then Bravo", Alpha="company", Bravo="company")
    assert out.text == "[COMPANY_1] then [COMPANY_2] then [COMPANY_1]"


def test_a_placeholder_is_labelled_by_kind():
    out = _redact(
        "Okafor at Vantara on okafor@vantara.com",
        Okafor="person",
        Vantara="company",
    )
    assert "[PERSON_1]" in out.text
    assert "[COMPANY_1]" in out.text
    assert "[EMAIL_1]" in out.text


# --------------------------------------------------------------------------- #
# Overlap and co-reference
# --------------------------------------------------------------------------- #


def test_a_longer_term_wins_over_a_shorter_one_at_the_same_place():
    out = _redact(
        "Meridian Capital Partners LLP filed",
        Meridian="company",
        **{"Meridian Capital Partners LLP": "company"},
    )
    assert "Capital" not in out.text
    assert "Partners" not in out.text
    assert "LLP" not in out.text


def test_a_company_named_three_ways_gets_one_placeholder():
    """Splitting one company across three placeholders is a silent miss.

    The model's whole value here is seeing that a margin quoted under one name is
    the same company as a margin quoted under another.
    """
    out = _redact(
        "Meridian Capital Partners LLP on the cover, "
        "Meridian Capital in the table, "
        "Meridian in the chart.",
        **{
            "Meridian Capital Partners LLP": "company",
            "Meridian Capital": "company",
            "Meridian": "company",
        },
    )
    assert set(PLACEHOLDER_RE.findall(out.text)) == {("COMPANY", "1")}
    assert out.text.count("[COMPANY_1]") == 3


def test_merging_only_applies_to_a_whole_word_prefix():
    out = _redact(
        "Meridian Group and Meridiana Holdings",
        **{"Meridian Group": "company", "Meridiana Holdings": "company"},
    )
    assert len(set(PLACEHOLDER_RE.findall(out.text))) == 2


def test_a_merged_term_restores_to_its_fullest_form():
    out = _redact(
        "Meridian Capital reported",
        **{"Meridian Capital Partners LLP": "company", "Meridian Capital": "company"},
    )
    assert out.restore("[COMPANY_1] margin") == "Meridian Capital Partners LLP margin"


# --------------------------------------------------------------------------- #
# Detected terms
# --------------------------------------------------------------------------- #


def test_a_detected_company_is_then_replaced_where_it_appears_bare():
    """Harvest first, replace second.

    The suffix is what identifies the name; the three places it appears without
    one are where the leak would be.
    """
    out = Redactor().apply(
        "Cover: Calderwood Holdings\nTable header: Calderwood\nChart: Calderwood"
    )
    assert "Calderwood" not in out.text
    assert out.text.count("[COMPANY_1]") == 3


def test_a_detected_term_records_where_it_came_from():
    out = Redactor().apply("Prepared by Jane Okafor, CFO")
    assert out.redactions[0].source == "detected:person"


def test_a_bare_company_head_is_redacted_when_it_is_distinctive():
    """The suffix identifies the name; the head is what a table header uses."""
    out = Redactor().apply(
        "Cover: Calderwood Holdings\nHeader: Calderwood\nChart: Calderwood"
    )
    assert "Calderwood" not in out.text
    assert out.text.count("[COMPANY_1]") == 3


def test_a_bare_company_head_that_is_an_ordinary_word_is_reported_not_redacted():
    """Redacting every "northern" would strip meaning for no privacy gain.

    So the bare form becomes a precise residual instead — neither a silent leak
    nor a blunt substitution.
    """
    out = Redactor().apply("Cover: Northern Trust\nHeader: Northern\nChart: Northern")
    (residual,) = out.residuals
    assert residual.text == "Northern"
    assert residual.occurrences == 2
    assert "Northern Trust" in residual.reason


def test_a_surname_used_alone_after_an_introduction_is_redacted():
    out = Redactor().apply("Prepared by Kwabena Osei-Bonsu, CFO.\nOsei-Bonsu added that")
    assert "Osei-Bonsu" not in out.text


def test_the_allowlist_suppresses_a_detection():
    out = Redactor(allowlist=["Company Management"]).apply(
        "Source: Company Management filings"
    )
    assert "Company Management" in out.text


# --------------------------------------------------------------------------- #
# Figures survive
# --------------------------------------------------------------------------- #

FIGURE_LINES = [
    "Revenue | 1,284 | 1,562 | 1,908 | 2,314 | 2,760",
    "Margin of 15.6% in 2025A against 15.8% in 2024A",
    "EBITDA of US$351 million, up 33.5% year on year",
    "Free cash flow of (42) rising to 168",
    "A range of 8.0x to 11.5x EV/EBITDA on 2026E",
    "Total 2023A-2027E | 9,828 | 1,808 | n.a. | 229",
]


@pytest.mark.parametrize("line", FIGURE_LINES)
def test_every_figure_survives_redaction_unchanged(line):
    """The bargain: the model sees the numbers and not whose they are."""
    out = Redactor({"Vantara": TermSource("company", "blocklist")}).apply(line)
    assert out.text == line


def test_a_figure_beside_a_redacted_name_is_untouched():
    out = _redact("Vantara revenue of 1,908.0 at a 15.6% margin", Vantara="company")
    assert out.text == "[COMPANY_1] revenue of 1,908.0 at a 15.6% margin"


# --------------------------------------------------------------------------- #
# Verification: the last line of defence
# --------------------------------------------------------------------------- #


def test_verify_finds_nothing_when_redaction_worked():
    out = _redact("Vantara grew", Vantara="company")
    assert out.verify(["Vantara"]) == ()


def test_verify_reports_a_term_that_survived():
    """The realistic failure is a rule that silently did not fire.

    Constructed here by verifying against a term the redactor was never given,
    which is the same observable state as a pattern that failed to match.
    """
    out = Redactor().apply("Alpine Ridge reported")
    assert out.verify(["Alpine Ridge"]) == ("Alpine Ridge",)


def test_verify_is_case_insensitive_and_whitespace_flexible():
    out = Redactor().apply("alpine\nridge reported")
    assert out.verify(["Alpine Ridge"]) == ("Alpine Ridge",)


# --------------------------------------------------------------------------- #
# Restoration
# --------------------------------------------------------------------------- #


def test_restore_puts_the_real_words_back():
    out = _redact("Vantara margin", Vantara="company")
    assert out.restore("[COMPANY_1] margin is 15.6%") == "Vantara margin is 15.6%"


def test_restore_leaves_an_unknown_placeholder_alone():
    """A model that invents [COMPANY_9] must not crash the report."""
    out = _redact("Vantara", Vantara="company")
    assert out.restore("[COMPANY_9] grew") == "[COMPANY_9] grew"


def test_restoration_is_not_part_of_what_is_sent():
    """The mapping lives on the Redacted object and not in its text."""
    out = _redact("Vantara", Vantara="company")
    assert "Vantara" not in out.text


# --------------------------------------------------------------------------- #
# Residuals
# --------------------------------------------------------------------------- #


def _residuals(
    corpus: str,
    *,
    allowlist: list[str] | None = None,
    detectors: tuple[Detector, ...] | None = None,
) -> list[str]:
    redactor = (
        Redactor(allowlist=allowlist or [])
        if detectors is None
        else Redactor(allowlist=allowlist or [], detectors=detectors)
    )
    return [residual.text for residual in redactor.apply(corpus).residuals]


def test_an_invented_name_left_behind_is_reported():
    assert "Halvorsen Estates" in _residuals(
        "We believe Halvorsen Estates remains the anchor tenant."
    )


def test_an_invented_name_in_a_title_is_reported_as_the_word_alone():
    """A heading capitalises every word, so the phrase carries no signal."""
    assert _residuals("Vantara Platform Drives Margin Expansion") == ["Vantara"]


def test_an_ordinary_sentence_raises_nothing():
    assert _residuals("Revenue rose 15.6% in the year to December 2025.") == []


def test_a_table_row_of_fiscal_years_raises_nothing():
    """The trailing letters of "2023A" are not words.

    Reading them as words turned a fiscal-year header into the capitalised
    phrase "A A A E E", which is the sort of noise that makes a reviewer stop
    reading the list.
    """
    assert _residuals("2023A | 2024A | 2025A | 2026E | 2027E") == []


def test_a_contents_page_of_roman_numerals_raises_nothing():
    assert _residuals("I. Situation | II. Valuation | III. Process | Section IV") == []


def test_a_surviving_at_sign_is_reported():
    assert any("@" in text for text in _residuals("write to deals at vantara@x"))


def test_a_surviving_web_address_is_reported():
    """With the detectors off, the residual scan is the only net left."""
    assert _residuals("see https://example.com/x", detectors=())


def test_a_long_digit_run_is_reported_but_a_figure_is_not():
    assert "4471209835512" in _residuals("ref 4471209835512.")
    assert _residuals("total of 1,908,000 and 12 345 678") == []


def test_a_residual_counts_its_occurrences_and_cites_its_first_line():
    out = Redactor().apply("a\nWe rate Halvorsen Estates highly\nHalvorsen Estates again")
    (residual,) = [r for r in out.residuals if r.text.startswith("Halvorsen")]
    assert residual.occurrences == 2
    assert residual.first_line == 2


def test_a_deck_with_residuals_is_not_clear():
    assert not Redactor().apply("We rate Halvorsen Estates highly").is_clear


def test_a_deck_without_residuals_is_clear():
    assert Redactor().apply("Revenue rose 15.6% in 2025.").is_clear


def test_a_placeholder_is_never_itself_a_residual():
    out = _redact("Vantara grew strongly", Vantara="company")
    assert out.residuals == ()


def test_the_allowlist_clears_a_residual():
    assert _residuals(
        "We rate Halvorsen Estates highly", allowlist=["Halvorsen Estates"]
    ) == []


def test_finance_vocabulary_is_not_reported():
    """A residual list full of EBITDA and CAGR is a list nobody reads."""
    assert _residuals("EBITDA and CAGR and IRR and LTM and Q3 and FY24") == []


def test_the_finance_vocabulary_is_stored_casefolded():
    assert all(word == word.casefold() for word in FINANCE_VOCABULARY)


# --------------------------------------------------------------------------- #
# Input hygiene
# --------------------------------------------------------------------------- #


def test_an_empty_or_whitespace_term_is_ignored():
    out = Redactor(
        {"   ": TermSource("custom", "blocklist"), "": TermSource("custom", "blocklist")}
    ).apply("Revenue rose")
    assert out.text == "Revenue rose"


def test_a_term_given_with_odd_whitespace_is_normalised():
    out = _redact("Meridian Capital reported", **{"Meridian  Capital": "company"})
    assert "Meridian" not in out.text


def test_redacting_an_empty_corpus_is_not_an_error():
    out = Redactor().apply("")
    assert out.text == ""
    assert out.is_clear
