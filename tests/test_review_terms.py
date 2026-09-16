"""Where the term list comes from.

Two failures matter here and they pull in opposite directions. Missing a source
under-redacts. Taking the wrong source over-redacts, and over-redaction is not
harmless: an early version pulled ``typography.canon_terms`` in and removed
"EBITDA" from the payload, which deletes the one thing the model was shown the
deck to reason about.
"""

from __future__ import annotations

import pytest

from tieout_review.terms import (
    assemble,
    blocklist_from_file,
    blocklist_from_text,
    terms_from_deck,
    terms_from_profile,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Acme, Project Atlas ,Jane Okafor", ["Acme", "Project Atlas", "Jane Okafor"]),
        ("Acme; Atlas\nOkafor", ["Acme", "Atlas", "Okafor"]),
        ("Acme,,, Acme ,acme", ["Acme"]),
        ("  Meridian   Capital  ", ["Meridian Capital"]),
        ("", []),
        (None, []),
        ("a, ab, abc", ["abc"]),
    ],
)
def test_the_blocklist_is_split_forgivingly(value, expected):
    """A list pasted out of a spreadsheet or an email arrives with whatever
    separator that tool used; being strict about it only under-redacts."""
    assert blocklist_from_text(value) == expected


def test_a_blocklist_file_supports_comments(tmp_path):
    path = tmp_path / "forbid.txt"
    path.write_text("# the target\nCalderwood\n# the adviser\nAshcombe Partners\n")
    assert blocklist_from_file(path) == ["Calderwood", "Ashcombe Partners"]


def test_the_client_name_comes_from_the_profile(reference_profile):
    terms = terms_from_profile(reference_profile)
    assert reference_profile.client in terms
    assert terms[reference_profile.client].source == "profile:client"


def test_the_learned_client_vocabulary_is_used(reference_profile):
    """The learner already wrote these down so TY-009 would not flag them.

    Nobody should have to type a client's proper nouns twice.
    """
    reference_profile.hygiene.dictionary = ["Calderwood", "Ellesmere", "Pre-tax"]
    terms = terms_from_profile(reference_profile)
    assert "Calderwood" in terms
    assert "Ellesmere" in terms


def test_an_ordinary_compound_in_the_learned_vocabulary_is_not_redacted(reference_profile):
    """``hygiene.dictionary`` holds both invented names and ordinary compounds a
    spell checker happens not to know. Redacting the second kind strips meaning
    for no privacy gain."""
    reference_profile.hygiene.dictionary = ["Pre-tax", "run-rate", "Calderwood"]
    terms = terms_from_profile(reference_profile)
    assert "Pre-tax" not in terms
    assert "run-rate" not in terms
    assert "Calderwood" in terms


def test_canonical_terminology_is_deliberately_not_a_source(reference_profile):
    """canon_terms records how to spell a term, not who it belongs to."""
    reference_profile.typography.canon_terms = {"EBITDA": ["Ebitda", "ebitda"]}
    assert "EBITDA" not in terms_from_profile(reference_profile)


def test_the_footer_boilerplate_is_a_source(reference_profile):
    """A confidentiality line usually names the firm."""
    texts = {entry.text for entry in reference_profile.brand.footer.boilerplate}
    terms = terms_from_profile(reference_profile)
    assert texts & set(terms)


def test_document_metadata_is_a_source(clean_deck):
    """docProps keeps the author and the firm long after anyone has looked.

    TieOut's HY-004 already reports those two fields as a hygiene defect; here
    they are evidence. Written against constructed metadata rather than a
    fixture so it asserts the mapping and not the fixture's contents.
    """
    import dataclasses

    package = dataclasses.replace(
        clean_deck.package,
        core=dataclasses.replace(
            clean_deck.package.core,
            creator="Jane Okafor",
            last_modified_by="Ade Balogun",
            keywords="Calderwood; Ellesmere",
        ),
        app=dataclasses.replace(clean_deck.package.app, company="Ashcombe Partners"),
    )
    terms = terms_from_deck(dataclasses.replace(clean_deck, package=package))
    assert terms["Jane Okafor"].source == "docProps:core.creator"
    assert terms["Ade Balogun"].source == "docProps:core.lastModifiedBy"
    assert terms["Ashcombe Partners"].source == "docProps:app.Company"
    assert "Calderwood" in terms
    assert "Ellesmere" in terms


def test_the_document_title_is_a_source(clean_deck):
    """A real catch that HY-004 does not even look at.

    ``docProps/core.xml`` on the reference deck carries the project codename in
    its title field, which no hygiene rule reports and which identifies the deal
    as precisely as anything on a slide does.
    """
    terms = terms_from_deck(clean_deck)
    assert terms["Project Meridian"].source == "docProps:core.title"


def test_the_blocklist_wins_on_a_collision(reference_profile, clean_deck):
    terms = assemble(
        profile=reference_profile, deck=clean_deck, blocklist=[reference_profile.client]
    )
    assert terms[reference_profile.client].source == "blocklist"


def test_assembly_emits_longer_terms_first():
    """The redactor needs it so that "Meridian Capital Partners LLP" is replaced
    rather than leaving "Partners LLP" behind."""
    terms = assemble(blocklist=["Meridian", "Meridian Capital Partners LLP"])
    assert list(terms) == ["Meridian Capital Partners LLP", "Meridian"]


def test_assembly_without_a_profile_or_deck_is_just_the_blocklist():
    assert list(assemble(blocklist=["Calderwood"])) == ["Calderwood"]


def test_a_two_character_term_is_refused():
    """A short blocklist entry matches inside half the deck's words."""
    assert assemble(blocklist=["AB"]) == {}
