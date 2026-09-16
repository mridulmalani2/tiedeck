"""Assembling the list of literal terms to redact.

Four sources, in descending order of how much the analyst controls them:

1. **Their blocklist.** A comma-separated list, typed once. "Meridian Capital,
   Project Atlas, Jane Okafor" is the whole interface, because that is what
   someone will actually fill in thirty seconds before sending a deck.
2. **The client's profile.** The learner already wrote down this client's
   vocabulary in order to spell-check it — ``hygiene.dictionary`` is a list of
   the proper nouns that appear in their decks and nowhere in a dictionary. The
   client name, the canonical terminology and the footer boilerplate (which
   names the firm) come from the same place. None of this has to be re-typed.
3. **The package's own metadata.** ``docProps`` keeps the author, the company
   and often the project name long after anyone has looked at it. TieOut's
   HY-004 already reports it as a hygiene defect; here it is a source of terms.
4. **The presets** — the detectors in :mod:`tieout_review.patterns`, run over the
   payload to harvest what nobody enumerated.

Keeping the assembly here rather than in :mod:`tieout_review.redact` means the
redaction engine takes a plain mapping and can be tested without a deck, a
profile or a file on disk.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final

from tieout.model.deck import DeckModel
from tieout.profile.schema import Profile
from tieout.text import is_common_word
from tieout_review.redact import FINANCE_VOCABULARY, TermSource


def _normalised(value: str) -> str:
    return " ".join(value.split()).casefold()

__all__ = [
    "assemble",
    "blocklist_from_file",
    "blocklist_from_text",
    "terms_from_deck",
    "terms_from_profile",
]

#: A term shorter than this is not worth redacting and is dangerous to: a
#: two-letter blocklist entry matches inside half the deck's words. The word
#: boundaries in :func:`tieout_review.redact._term_pattern` stop the worst of it,
#: but "AB" as a term still redacts the axis label "AB" everywhere.
_MIN_TERM_LENGTH: Final[int] = 3

#: Profile fields that hold a client's own vocabulary rather than a convention.
_VOCABULARY_NOTE: Final[str] = "profile:hygiene.dictionary"


def blocklist_from_text(value: str | None) -> list[str]:
    """Split the comma-separated list an analyst types.

    Semicolons and newlines are accepted too, because a list pasted out of a
    spreadsheet or an email arrives with whichever separator that tool used and
    being strict about it would only produce a silent under-redaction.
    """
    if not value:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for chunk in value.replace(";", ",").replace("\n", ",").split(","):
        term = " ".join(chunk.split())
        key = term.casefold()
        if len(term) >= _MIN_TERM_LENGTH and key not in seen:
            seen.add(key)
            out.append(term)
    return out


def blocklist_from_file(path: Path) -> list[str]:
    """Read a blocklist from a file: one term per line, or comma-separated.

    Lines beginning with ``#`` are comments, so the file can record why a term
    is on the list.
    """
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    return blocklist_from_text("\n".join(lines))


def terms_from_profile(profile: Profile) -> dict[str, TermSource]:
    """Terms the learner already recorded for this client."""
    out: dict[str, TermSource] = {}

    def add(term: str | None, kind: str, source: str) -> None:
        cleaned = " ".join((term or "").split())
        if len(cleaned) >= _MIN_TERM_LENGTH:
            out.setdefault(cleaned, TermSource(kind, source))

    add(profile.client, "company", "profile:client")

    # ``typography.canon_terms`` is deliberately NOT a source. It records how to
    # spell a term, not who a term belongs to, and its entries are the deck's
    # own subject matter: redacting it took "EBITDA" out of the payload, which
    # removes the one thing the model was shown the deck to reason about.
    for word in profile.hygiene.dictionary:
        if _is_distinctive(word):
            add(word, "custom", _VOCABULARY_NOTE)

    for entry in profile.brand.footer.boilerplate:
        add(entry.text, "custom", "profile:brand.footer.boilerplate")

    return out


def _is_distinctive(word: str) -> bool:
    """Whether a dictionary entry identifies someone rather than describing something.

    The learner seeds ``hygiene.dictionary`` from the reference deck's own
    vocabulary so that TY-009 does not flag a client's proper nouns as
    misspellings. That list therefore holds both kinds of word: the invented
    names ("Calderwood", "Ellesmere") and the ordinary compounds a spell checker
    happens not to know ("Pre-tax", "run-rate"). Redacting the second kind
    strips meaning out of the payload for no privacy gain, so only capitalised
    entries whose parts are not ordinary English are taken.
    """
    cleaned = " ".join(word.split())
    if not cleaned or not cleaned[:1].isupper():
        return False
    if _normalised(cleaned) in FINANCE_VOCABULARY:
        return False
    parts = [part for part in re.split(r"[\s\-/]+", cleaned) if part]
    return any(not is_common_word(part.strip(".'\u2019")) for part in parts)


def terms_from_deck(deck: DeckModel) -> dict[str, TermSource]:
    """Terms the package metadata gives away.

    ``docProps`` is where a deck keeps the author's name, the firm's name and,
    in the title field, the project codename — long after anyone has thought
    about it. The same fields TieOut's HY-004 reports as a hygiene defect.
    """
    out: dict[str, TermSource] = {}

    def add(term: str | None, kind: str, source: str) -> None:
        cleaned = " ".join((term or "").split())
        if len(cleaned) >= _MIN_TERM_LENGTH:
            out.setdefault(cleaned, TermSource(kind, source))

    core = deck.package.core
    add(core.creator, "person", "docProps:core.creator")
    add(core.last_modified_by, "person", "docProps:core.lastModifiedBy")
    add(core.title, "custom", "docProps:core.title")
    add(core.subject, "custom", "docProps:core.subject")
    add(core.category, "custom", "docProps:core.category")
    for keyword in (core.keywords or "").replace(";", ",").split(","):
        add(keyword, "custom", "docProps:core.keywords")

    add(deck.package.app.company, "company", "docProps:app.Company")
    add(deck.package.app.manager, "person", "docProps:app.Manager")

    for author in deck.package.comment_authors:
        add(author, "person", "docProps:comment author")

    return out


def assemble(
    *,
    profile: Profile | None = None,
    deck: DeckModel | None = None,
    blocklist: Iterable[str] = (),
) -> dict[str, TermSource]:
    """Merge every source into one term list.

    The blocklist wins on a collision, so an analyst who classifies a term
    themselves overrides whatever the profile or the metadata called it. Longer
    terms are emitted first, which is what the redactor needs in order to
    replace "Meridian Capital Partners LLP" rather than leaving "Partners LLP"
    behind.
    """
    out: dict[str, TermSource] = {}
    for term in blocklist:
        cleaned = " ".join(term.split())
        if len(cleaned) >= _MIN_TERM_LENGTH:
            out.setdefault(cleaned, TermSource("custom", "blocklist"))
    if profile is not None:
        for term, source in terms_from_profile(profile).items():
            out.setdefault(term, source)
    if deck is not None:
        for term, source in terms_from_deck(deck).items():
            out.setdefault(term, source)
    return dict(sorted(out.items(), key=lambda item: (-len(item[0]), item[0].casefold())))


def iter_sources(terms: dict[str, TermSource]) -> Iterator[tuple[str, str, int]]:
    """``(source, kind, count)`` per origin, for the summary an analyst reads."""
    tally: dict[tuple[str, str], int] = {}
    for source in terms.values():
        key = (source.source, source.kind)
        tally[key] = tally.get(key, 0) + 1
    for (source_name, kind), count in sorted(tally.items()):
        yield source_name, kind, count
