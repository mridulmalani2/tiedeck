"""Deriving terminology canon candidates.

Terminology cannot be derived outright, and pretending otherwise would be the
most damaging kind of false confidence: a canon rule enforcing the wrong spelling
of a client's own name across every future deck.

So this deriver does not decide anything. It finds groups of capitalised phrases
that normalise to the same key but appear in more than one surface form, proposes
the most frequent as canonical, and hands the decision to the interview. When the
variants are explained by sentence-initial capitalisation it does not even ask,
because "Revenue growth" at the start of a sentence and "revenue growth" in the
middle of one is not a terminology question and offering it as one is how a
twelve-question budget gets spent on nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from tieout.learn.classify import Derivation, QuestionDraft
from tieout.learn.observe import iter_runs, learnable_slides
from tieout.model.deck import DeckModel
from tieout.model.furniture import Furniture
from tieout.text import (
    TRAILING_PUNCTUATION,
    canon_key,
    capitalised_ngrams,
    clean_term,
    is_common_word,
    word_windows,
)

#: A phrase must occur at least this often to be worth considering.
MIN_OCCURRENCES: Final[int] = 3

#: Longest phrase considered, per section 8.3.
MAX_NGRAM: Final[int] = 4


@dataclass
class SurfaceForm:
    """One spelling of a term, and where it appears."""

    text: str
    count: int = 0
    slides: set[int] = field(default_factory=set)
    #: True when every occurrence begins a sentence, where capitalisation is
    #: forced and therefore says nothing about the house convention.
    always_sentence_initial: bool = True


@dataclass
class CanonCandidate:
    """A normalised term with more than one observed spelling."""

    key: str
    forms: list[SurfaceForm]

    @property
    def canonical(self) -> SurfaceForm:
        """The most frequent spelling, ties broken alphabetically for determinism."""
        return max(self.forms, key=lambda f: (f.count, f.text))

    @property
    def variants(self) -> list[SurfaceForm]:
        canonical = self.canonical
        return [f for f in self.forms if f.text != canonical.text]

    @property
    def total(self) -> int:
        return sum(f.count for f in self.forms)

    @property
    def differs_only_by_case(self) -> bool:
        """Whether every spelling is the same term in different capitalisation.

        Case is typography, not terminology. A deck sets a section name in caps
        in the eyebrow, in title case on the agenda and in sentence case in
        prose, and all three are correct. ``typography.title_case`` is the rule
        that governs casing; asking the user to pick one spelling here, and then
        reporting the other two hundreds of times, is the same judgement made
        twice and wrongly the second time.
        """
        return len({f.text.casefold() for f in self.forms}) == 1

    @property
    def explained_by_sentence_position(self) -> bool:
        """Whether the variation is only sentence-initial capitalisation.

        True when the spellings differ solely in case and every occurrence of at
        least one of them starts a sentence.
        """
        spellings = {f.text for f in self.forms}
        if len({s.casefold() for s in spellings}) > 1:
            return False
        return any(f.always_sentence_initial for f in self.forms)


@dataclass
class TermsDerivation:
    """Proposed canon entries and the questions needed to confirm them."""

    #: Canonical form -> variants. Empty values mean "canonical, no variants seen",
    #: which still earns an entry so TY-005 can catch a future misspelling.
    canon_terms: dict[str, list[str]] = field(default_factory=dict)
    #: Canonical form -> the other spellings the reference deck itself uses.
    #: Accepted rather than reported: see ``TypographyProfile.canon_accepted``.
    canon_accepted: dict[str, list[str]] = field(default_factory=dict)
    candidates: list[CanonCandidate] = field(default_factory=list)
    #: Words from the reference deck that a general dictionary rejects, offered
    #: to ``hygiene.dictionary`` so the spell check does not report the client's
    #: own vocabulary.
    vocabulary: list[str] = field(default_factory=list)
    derivation: Derivation = field(default_factory=Derivation)


def derive_terms(deck: DeckModel, furniture: Furniture) -> TermsDerivation:
    """Find terminology variants and draft the questions to resolve them."""
    result = TermsDerivation()
    groups = collect_phrases(deck, furniture)

    frequent = {
        key: forms
        for key, forms in groups.items()
        if sum(f.count for f in forms.values()) >= MIN_OCCURRENCES
    }

    for key, forms in sorted(frequent.items()):
        candidate = CanonCandidate(key=key, forms=sorted(forms.values(), key=lambda f: f.text))
        if len(candidate.forms) == 1:
            # One consistent spelling that recurs is worth recording even with no
            # variants: it gives TY-005 something to check a future deck against,
            # which is the whole point of a canon.
            only = candidate.forms[0]
            if only.count >= MIN_OCCURRENCES and _is_distinctive(only.text):
                result.canon_terms.setdefault(only.text, [])
                result.derivation.note(
                    f"typography.canon_terms.{only.text}",
                    f"one consistent spelling, {only.count} occurrences across "
                    f"{len(only.slides)} slides",
                    "high",
                )
            continue

        if not _is_distinctive(candidate.canonical.text):
            continue

        if candidate.differs_only_by_case:
            # Recorded with no variants: the spellings seen here are all correct,
            # and listing them would have TY-005 report the reference deck's own
            # headings. Casing is opted into per term via
            # ``typography.canon_case_sensitive``.
            result.canon_terms.setdefault(candidate.canonical.text, [])
            spellings = ", ".join(sorted(f.text for f in candidate.forms))
            result.derivation.note(
                f"typography.canon_terms.{candidate.canonical.text}",
                f"spellings differ only in capitalisation ({spellings}), which is "
                "typography rather than terminology; all are accepted. Add the term "
                "to typography.canon_case_sensitive to enforce one casing",
                "high",
            )
            continue

        result.candidates.append(candidate)
        result.derivation.ask(_canon_question(candidate))

    result.canon_terms = _drop_subsumed(result.canon_terms, groups)
    result.canon_accepted = _accepted_forms(deck, furniture, result.canon_terms)
    result.vocabulary = _client_vocabulary(groups)
    if result.vocabulary:
        result.derivation.note(
            "hygiene.dictionary",
            f"{len(result.vocabulary)} word(s) used in the reference deck that a "
            f"general dictionary rejects, so the spell check does not report the "
            f"client's own names: "
            + ", ".join(result.vocabulary[:8])
            + ("..." if len(result.vocabulary) > 8 else ""),
            "high",
        )

    if not result.canon_terms and not result.candidates:
        result.derivation.unlearned(
            "typography.canon_terms",
            f"no capitalised phrase occurs at least {MIN_OCCURRENCES} times",
        )
    return result


def _accepted_forms(
    deck: DeckModel,
    furniture: Furniture,
    canon: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Every other spelling of a canonical term that the reference deck uses.

    Chrome is included here although it is excluded from term *derivation*. The
    logo wordmark is furniture and must not vote on what the deck's terms are --
    admitting it would make the confidentiality line the most frequent phrase in
    any deck by an order of magnitude. But it is text on the slide, TY-005 reads
    it, and a house that sets its own name in capitals in its logo and in title
    case in its disclaimer uses both spellings. Leaving the wordmark out here
    reports it on every slide in the deck it was learned from.
    """
    by_length: dict[int, dict[str, str]] = {}
    for term in canon:
        key = canon_key(term)
        length = len(key.split())
        if length:
            by_length.setdefault(length, {})[key] = term
    if not by_length:
        return {}
    observed: dict[str, set[str]] = {term: set() for term in canon}

    def ingest(text: str) -> None:
        for length, keys in by_length.items():
            for window in word_windows(text, length):
                surface = window.rstrip(TRAILING_PUNCTUATION)
                canonical = keys.get(canon_key(surface))
                if canonical is not None and surface != canonical:
                    observed[canonical].add(surface)

    for context in iter_runs(deck, furniture, include_furniture=True):
        ingest(context.run.text)
    for slide in deck.slides:
        for shape in slide.charts:
            if shape.chart is not None:
                for text in shape.chart.text_strings:
                    ingest(text)

    # A spelling the interview declared wrong stays wrong, however often the
    # reference deck uses it: that is what answering the question decided.
    return {
        canonical: sorted(forms - set(canon.get(canonical, ())))
        for canonical, forms in observed.items()
        if forms - set(canon.get(canonical, ()))
    }


def _client_vocabulary(
    groups: dict[str, dict[str, SurfaceForm]],
) -> list[str]:
    """Words the reference deck uses that a general dictionary does not contain.

    Project codenames, client names, counterparty names. Seeding these from the
    deck is what makes TY-009 usable at all: without it the rule's first run on
    any real deck is a list of the client's own proper nouns, and a spell checker
    that cries wolf once gets switched off for good.

    The cost, stated in the README's limitations: a proper noun that is actually
    misspelled in the reference deck is learned as correct. That is the same
    trade the whole tool makes -- the reference deck is treated as ground truth --
    and TY-009 ships disabled in any case.
    """
    words: set[str] = set()
    for forms in groups.values():
        for form in forms.values():
            for word in form.text.split():
                cleaned = clean_term(word)
                if len(cleaned) < 3 or cleaned.isupper():
                    continue
                if not is_common_word(cleaned):
                    words.add(cleaned)
    return sorted(words)


def _drop_subsumed(
    canon: dict[str, list[str]], groups: dict[str, dict[str, SurfaceForm]]
) -> dict[str, list[str]]:
    """Remove a term that only ever occurs inside a longer term.

    "Ashcombe" and "Ashcombe Partners" both clear the frequency and
    distinctiveness bars, but they are one term, and emitting both would have
    TY-005 report the same text twice. The longer form wins when it accounts for
    every occurrence of the shorter.
    """
    counts = {
        term: sum(form.count for form in groups.get(canon_key(term), {}).values())
        for term in canon
    }
    out: dict[str, list[str]] = {}
    for term, variants in canon.items():
        words = term.split()
        subsumed = any(
            other != term
            and len(other.split()) > len(words)
            and _is_subsequence(words, other.split())
            and counts.get(other, 0) >= counts.get(term, 0)
            for other in canon
        )
        if not subsumed:
            out[term] = variants
    return out


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """Whether ``needle`` appears as a contiguous run inside ``haystack``."""
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[start : start + len(needle)] == needle
        for start in range(len(haystack) - len(needle) + 1)
    )


def collect_phrases(
    deck: DeckModel, furniture: Furniture
) -> dict[str, dict[str, SurfaceForm]]:
    """Normalisation key -> surface form -> occurrences.

    Furniture is excluded. The confidentiality line is on every slide, and
    admitting it would make "Strictly Private And Confidential" the deck's most
    frequent term by an order of magnitude while telling us nothing.
    """
    groups: dict[str, dict[str, SurfaceForm]] = {}

    def ingest(text: str, slide_index: int) -> None:
        for raw, sentence_initial in capitalised_ngrams(text, MAX_NGRAM):
            phrase = clean_term(raw)
            key = canon_key(phrase)
            if not key or not phrase:
                continue
            forms = groups.setdefault(key, {})
            form = forms.get(phrase)
            if form is None:
                form = SurfaceForm(text=phrase)
                forms[phrase] = form
            form.count += 1
            form.slides.add(slide_index)
            if not sentence_initial:
                form.always_sentence_initial = False

    for context in iter_runs(deck, furniture):
        ingest(context.run.text, context.slide.index)

    for slide in learnable_slides(deck):
        for shape in slide.charts:
            if shape.chart is None:
                continue
            for text in shape.chart.text_strings:
                ingest(text, slide.index)

    return groups


def _is_distinctive(phrase: str) -> bool:
    """Whether a phrase is specific enough to be worth a canon entry.

    A term is distinctive when at least one of its words is not ordinary English:
    a client name, a project codename, an invented product. "Ashcombe Partners"
    qualifies; "Board of Directors", "Revenue" and "These" do not, however often
    they recur.

    This matters more than it looks. Without it the canon fills with two dozen
    ordinary capitalised words, and since every one of them is a potential
    terminology question, the interview's twelve-question budget is spent before
    it reaches anything the user actually needs to decide.
    """
    words = [word.strip("'\u2019") for word in phrase.split()]
    words = [word for word in words if word]
    if not words:
        return False
    if any(
        word.isupper() and len(word) > 2 and not is_common_word(word) for word in words
    ):
        # An acronym or a wordmark set in capitals is a term by construction --
        # but an ordinary English word set in capitals is a heading, and "KEY",
        # "GROSS" and "PERFORMANCE" are not this client's vocabulary.
        return True
    return any(len(word) > 3 and not is_common_word(word) for word in words)


def _canon_question(candidate: CanonCandidate) -> QuestionDraft:
    canonical = candidate.canonical
    listing = ", ".join(
        f"{form.text!r} ({form.count})"
        for form in sorted(candidate.forms, key=lambda f: (-f.count, f.text))
    )
    slides = tuple(sorted({s for form in candidate.forms for s in form.slides}))
    return QuestionDraft(
        id=f"canon-{candidate.key.replace(' ', '-')}",
        field_path="typography.canon_terms",
        question=f"Both {listing} appear. Which is the canonical form?",
        options=[form.text for form in sorted(candidate.forms, key=lambda f: -f.count)],
        default=canonical.text,
        impact=sum(form.count for form in candidate.variants),
        kind="terminology",
        slides=slides,
    )


def apply_canon_answers(
    result: TermsDerivation, answers: dict[str, str]
) -> dict[str, list[str]]:
    """Fold interview answers into the canon map.

    An answered question locks the canonical form and makes every other observed
    spelling a variant TY-005 will report.
    """
    canon = dict(result.canon_terms)
    for candidate in result.candidates:
        question_id = f"canon-{candidate.key.replace(' ', '-')}"
        chosen = answers.get(question_id, candidate.canonical.text)
        variants = sorted(
            form.text for form in candidate.forms if form.text != chosen
        )
        canon[chosen] = variants
    return canon
