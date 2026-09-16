"""Redaction: the security boundary of this package.

Everything in ``tieout`` proper is air-gapped and provably so. This package
breaks that on purpose, for one narrow reason: some consistency questions are
genuinely semantic and no amount of arithmetic answers them. The bargain is that
the model is allowed to see the *figures* and the *shape of the argument* and
never who it is about. It should be able to tell you that a headline claiming
20% growth sits above a table showing 8%, without being able to tell you whose
deck it was.

This module is what makes that bargain hold, so it is written to be read:

* **Fail closed.** :meth:`Redactor.apply` returns a :class:`Redacted` carrying
  both the redactions it made and the *residuals* — text it could not confidently
  clear. The orchestration layer will not transmit a payload with residuals
  outstanding unless a person has looked at them. Nothing here decides on its
  own that a payload is safe.
* **Verify, don't trust.** :meth:`Redacted.verify` re-checks the outgoing text
  for every literal term the redactor was given. A regex that silently failed to
  match is the realistic failure mode, and a bug in the code above must not be
  able to leak anything.
* **Numbers are never touched.** That is the whole point.
* **Placeholders are stable and typed.** The same company is ``[COMPANY_1]``
  everywhere in the payload, so the model can still reason about co-reference:
  "[COMPANY_1] margin is 15.6% on slide 4 and 15.8% on slide 12" is a usable
  finding.
* **Restoration is local only.** :meth:`Redacted.restore` puts the real words
  back into the model's own output, so the person reading a finding sees their
  deck's language. That happens on the analyst's machine, after the response has
  arrived; the mapping is never transmitted.

The known limit, stated plainly because the design depends on admitting it: a
one-word invented name that happens to be an ordinary English word — "Meridian",
"Atlas", "Vantage" — cannot be distinguished from prose by any rule here. That
is precisely why the residual list exists and why it is reviewed rather than
suppressed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from tieout.text import capitalisation_style, is_common_word
from tieout_review.patterns import DETECTORS, Detector, Span, detect, strip_org_suffix

__all__ = [
    "PLACEHOLDER_RE",
    "Redacted",
    "Redaction",
    "Redactor",
    "Residual",
    "TermSource",
]


@dataclass(frozen=True, slots=True)
class TermSource:
    """Why a literal term is on the list, for the audit trail the analyst reads."""

    kind: str
    source: str


@dataclass(frozen=True, slots=True)
class Redaction:
    """One term that was replaced, and everywhere it was replaced."""

    kind: str
    original: str
    placeholder: str
    source: str
    occurrences: int


@dataclass(frozen=True, slots=True)
class Residual:
    """Text the redactor could not clear, for a person to rule on.

    ``reason`` is written to be read by someone deciding in two seconds whether
    this is a client identifier or an ordinary word.
    """

    text: str
    reason: str
    occurrences: int
    first_line: int


#: The placeholder shape. Square brackets are rare in deck prose and the form is
#: unambiguous enough that the residual scan can recognise its own output.
PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"\[([A-Z]+)_(\d+)\]")

#: Kinds in the order their placeholders are most useful to a reader.
_KIND_LABELS: Final[dict[str, str]] = {
    "company": "COMPANY",
    "person": "PERSON",
    "codename": "CODENAME",
    "email": "EMAIL",
    "phone": "PHONE",
    "url": "URL",
    "path": "PATH",
    "ticker": "TICKER",
    "handle": "HANDLE",
    "address": "ADDRESS",
    "postcode": "POSTCODE",
    "custom": "TERM",
}

#: Vocabulary that is capitalised in a banking deck for reasons that have nothing
#: to do with identity. Without this the residual list is unreadable and would be
#: ignored, which is the worst outcome for a control that depends on being read.
FINANCE_VOCABULARY: Final[frozenset[str]] = frozenset(
    word.casefold()
    for word in (
        # Metrics and measures
        "EBITDA", "EBIT", "EBITDAR", "NOPAT", "CAGR", "IRR", "MOIC", "ROIC", "ROE",
        "ROA", "ROCE", "WACC", "DCF", "NPV", "EPS", "DPS", "EV", "NAV", "AUM", "AUA",
        "FCF", "FCFE", "FCFF", "COGS", "SG&A", "CapEx", "OpEx", "ARR", "MRR", "ACV",
        "TCV", "GMV", "TAM", "SAM", "SOM", "KPI", "KPIs", "CAC", "LTV", "NPS", "DSO",
        "DPO", "DIO", "NWC", "PF", "LTM", "NTM", "TTM", "YTD", "MTD", "QTD", "YoY",
        "QoQ", "MoM", "CY", "FY", "PY", "BPS", "GM", "OM", "NM", "PP", "PPT",
        # Transactions and process
        "M&A", "LBO", "MBO", "IPO", "SPAC", "JV", "LOI", "NDA", "DD", "QoE", "SPA",
        "APA", "ROFR", "ROFO", "PIK", "TEV", "LP", "GP", "PE", "VC", "IC", "IM",
        "CIM", "CIP", "VDR", "RFP", "SOW", "TSA", "TSR", "EGM", "AGM",
        # Accounting and reporting
        "GAAP", "IFRS", "US", "USD", "EUR", "GBP", "CHF", "JPY", "CNY", "HKD", "SGD",
        "AUD", "CAD", "INR", "SEK", "NOK", "DKK", "BRL", "ZAR", "AED", "SAR",
        "EBITDA-to-Revenue", "P&L", "BS", "CF", "YE", "HY", "H1", "H2",
        "Q1", "Q2", "Q3", "Q4", "FY24", "FY25",
        # Periods
        "January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December",
        "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct",
        "Nov", "Dec", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday",
        # Deck furniture
        "Appendix", "Agenda", "Exhibit", "Annex", "Schedule", "Source", "Sources",
        "Note", "Notes", "Footnote", "Confidential", "Draft", "Preliminary",
        "Illustrative", "Indicative", "Selected", "Adjusted", "Pro", "Forma",
        "Management", "Case", "Base", "Upside", "Downside", "Actual", "Budget",
        "Plan", "Forecast", "Estimate", "Consensus", "Reported", "Underlying",
        "Total", "Subtotal", "Other", "Various", "Memo", "Memorandum",
        "Disclaimer", "Important", "Contents", "Overview", "Summary", "Executive",
        # Section numbering. A deck's contents page is roman numerals and nothing
        # else, and every one of them would otherwise read as an unknown word.
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
    )
)


def _normalise(value: str) -> str:
    """Collapse whitespace and case, for matching a term to an earlier one."""
    return " ".join(value.split()).casefold()


def _term_pattern(term: str) -> re.Pattern[str] | None:
    """A whitespace-flexible, case-insensitive pattern for one literal term.

    A company name that wraps across two lines in a text box arrives as
    "Meridian\\nCapital", so matching the literal string would miss it. Word
    boundaries are applied only where the term itself starts or ends in a word
    character, so "S.p.A." and "&Co" still match.
    """
    words = term.split()
    if not words:
        return None
    body = r"[\s ]+".join(re.escape(word) for word in words)
    prefix = r"\b" if term[:1].isalnum() or term[:1] == "_" else ""
    suffix = r"\b" if term[-1:].isalnum() or term[-1:] == "_" else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


_AT_SIGN: Final[re.Pattern[str]] = re.compile(r"\S*@\S*")
_SCHEME: Final[re.Pattern[str]] = re.compile(r"(?:https?://|www\.|ftp://|\S+\.(?:com|net|org|io|ai|co\.uk))")
#: Nine or more digits with no decimal point is an identifier, not a figure: a
#: revenue line in thousands tops out well below that, and an account number,
#: a registration number or an unseparated phone number does not.
_LONG_DIGITS: Final[re.Pattern[str]] = re.compile(r"(?<![\d.])\d{9,}(?!\.?\d)")
#: A word, not a letter inside something else. The lookbehind is what stops the
#: "A" of "2023A" and the "E" of "2027E" from being read as words, which turned
#: a fiscal-year table row into the capitalised phrase "A A A E E".
#: A short form below this length is not worth redacting and is dangerous to.
_MIN_SHORT_FORM: Final[int] = 3

_WORD: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z'’&.\-]+")
_SENTENCE_START: Final[re.Pattern[str]] = re.compile(r"(?:^|[.!?:;|]\s*|•\s*)$")


@dataclass(slots=True)
class Redacted:
    """A payload with identifying text replaced, and the evidence for both halves."""

    text: str
    redactions: tuple[Redaction, ...]
    residuals: tuple[Residual, ...]
    _restore_map: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def is_clear(self) -> bool:
        """Whether nothing is outstanding. Transmission requires this or a person."""
        return not self.residuals

    def verify(self, terms: Iterable[str]) -> tuple[str, ...]:
        """Literal terms still present in the redacted text.

        The last line of defence, and the reason it exists: the failure that
        actually happens is not a missing rule, it is a rule that silently did
        not fire — a term with a soft hyphen in it, a pattern that backtracked.
        A non-empty result here means a bug in this module and must stop the run.
        """
        offenders: list[str] = []
        for term in terms:
            pattern = _term_pattern(term)
            if pattern is not None and pattern.search(self.text):
                offenders.append(term)
        return tuple(offenders)

    def restore(self, value: str) -> str:
        """Put the real words back, for local display only.

        The model answers in terms of ``[COMPANY_1]``; the analyst needs to read
        the finding in terms of their own deck. This runs on the analyst's
        machine after the response has arrived, and the mapping it uses is never
        transmitted anywhere.
        """

        def replace(match: re.Match[str]) -> str:
            return self._restore_map.get(match.group(0), match.group(0))

        return PLACEHOLDER_RE.sub(replace, value)


class Redactor:
    """Replaces identifying text with stable placeholders.

    ``terms`` are literal strings known in advance: the client name from the
    profile, the analyst's comma-separated blocklist, the names the learner
    already recorded as this client's vocabulary, whatever ``docProps`` left
    behind. ``detectors`` find the rest — the contact details, locators and
    name-shaped phrases nobody can enumerate up front.

    The two work together in one pass so that overlaps resolve sensibly: a
    blocklist entry "Meridian" and a detected "Meridian Capital Partners LLP"
    produce one placeholder for the longer span rather than a half-redacted
    fragment that reads as safe.
    """

    def __init__(
        self,
        terms: Mapping[str, TermSource] | None = None,
        *,
        detectors: Sequence[Detector] = DETECTORS,
        allowlist: Iterable[str] = (),
    ) -> None:
        self._terms: dict[str, TermSource] = {}
        for term, source in (terms or {}).items():
            cleaned = " ".join(term.split())
            if cleaned:
                self._terms.setdefault(cleaned, source)
        self._detectors = tuple(detectors)
        self._allowlist = frozenset(_normalise(entry) for entry in allowlist if entry.strip())

    @property
    def terms(self) -> dict[str, TermSource]:
        return dict(self._terms)

    def harvest(self, corpus: str) -> dict[str, TermSource]:
        """Terms the detectors find in ``corpus``, as literals for reuse.

        Harvesting separately from replacing is what makes a redaction
        consistent: "Meridian Capital Partners LLP" is detected where it carries
        its suffix and then replaced everywhere, including the three places it
        appears bare in a table header where no detector would have fired.
        """
        found: dict[str, TermSource] = {}
        for span in detect(corpus, self._detectors):
            cleaned = " ".join(span.text.split())
            if not cleaned or _normalise(cleaned) in self._allowlist:
                continue
            found.setdefault(cleaned, TermSource(span.kind, f"detected:{span.kind}"))
            for short in _short_forms(cleaned, span.kind):
                if _normalise(short) not in self._allowlist and _is_distinctive(short):
                    found.setdefault(
                        short, TermSource(span.kind, f"detected:{span.kind} (short form)")
                    )
        return found

    def apply(self, corpus: str) -> Redacted:
        """Redact ``corpus`` whole.

        The whole payload is redacted in one call rather than item by item so
        that placeholder numbering is stable across it. Numbering follows first
        appearance, which makes a diff between two runs of the same deck
        readable.
        """
        terms = dict(self._terms)
        for term, source in self.harvest(corpus).items():
            terms.setdefault(term, source)

        spans = self._spans(corpus, terms)
        text, redactions, restore_map = self._rewrite(corpus, spans, terms, _merge_map(terms))
        residuals = _scan_residuals(text, self._allowlist) + self._bare_forms(text, terms)
        return Redacted(
            text=text,
            redactions=redactions,
            residuals=residuals,
            _restore_map=restore_map,
        )

    def _bare_forms(self, text: str, terms: Mapping[str, TermSource]) -> tuple[Residual, ...]:
        """Short forms of a redacted name that are ordinary words and survive.

        "Calderwood Holdings" reduces to "Calderwood", which is not an English
        word, so it is redacted outright. "Northern Trust" reduces to "Northern",
        which is — and redacting every "northern" in a deck would take meaning
        out of the payload for no gain. So the bare form is reported instead: it
        is a precise, actionable residual rather than either a silent leak or a
        blunt substitution.
        """
        out: list[Residual] = []
        seen: set[str] = set()
        for term, source in terms.items():
            if source.kind not in ("company", "codename", "person"):
                continue
            for short in _short_forms(term, source.kind):
                key = _normalise(short)
                if key in seen or key in self._allowlist or _is_distinctive(short):
                    continue
                pattern = _term_pattern(short)
                if pattern is None:
                    continue
                hits = pattern.findall(text)
                if not hits:
                    continue
                seen.add(key)
                out.append(
                    Residual(
                        text=short,
                        reason=(
                            f"part of the redacted name {term!r}, appearing on its own; "
                            "it is an ordinary word, so it was not redacted"
                        ),
                        occurrences=len(hits),
                        first_line=_first_line(text, pattern),
                    )
                )
        return tuple(out)

    def _spans(self, corpus: str, terms: Mapping[str, TermSource]) -> list[Span]:
        """Every span to replace, longest-first on overlap.

        Longer wins so that a partial redaction can never be left behind: half a
        company name reads as cleared and is not.
        """
        candidates: list[Span] = []
        for term, source in terms.items():
            pattern = _term_pattern(term)
            if pattern is None:
                continue
            for match in pattern.finditer(corpus):
                candidates.append(
                    Span(
                        kind=source.kind,
                        text=match.group(0),
                        start=match.start(),
                        end=match.end(),
                    )
                )

        candidates.sort(key=lambda span: (span.start, -(span.end - span.start)))
        kept: list[Span] = []
        reach = 0
        for span in candidates:
            if span.start < reach:
                continue
            kept.append(span)
            reach = span.end
        return kept

    def _rewrite(
        self,
        corpus: str,
        spans: Sequence[Span],
        terms: Mapping[str, TermSource],
        merge: Mapping[str, str],
    ) -> tuple[str, tuple[Redaction, ...], dict[str, str]]:
        by_normalised: dict[str, TermSource] = {
            _normalise(term): source for term, source in terms.items()
        }
        longest: dict[str, str] = {}
        for term in terms:
            key = merge.get(_normalise(term), _normalise(term))
            current = longest.get(key)
            cleaned = " ".join(term.split())
            if current is None or len(cleaned) > len(current):
                longest[key] = cleaned
        assigned: dict[str, str] = {}
        canonical: dict[str, str] = {}
        counters: dict[str, int] = {}
        counts: dict[str, int] = {}
        pieces: list[str] = []
        cursor = 0

        for span in spans:
            key = merge.get(_normalise(span.text), _normalise(span.text))
            if key not in assigned:
                source = by_normalised.get(key, TermSource(span.kind, f"detected:{span.kind}"))
                label = _KIND_LABELS.get(source.kind, _KIND_LABELS["custom"])
                counters[label] = counters.get(label, 0) + 1
                assigned[key] = f"[{label}_{counters[label]}]"
                canonical[key] = longest.get(key, " ".join(span.text.split()))
            counts[key] = counts.get(key, 0) + 1
            pieces.append(corpus[cursor : span.start])
            pieces.append(assigned[key])
            cursor = span.end
        pieces.append(corpus[cursor:])

        redactions = tuple(
            Redaction(
                kind=by_normalised.get(key, TermSource("custom", "detected")).kind,
                original=canonical[key],
                placeholder=placeholder,
                source=by_normalised.get(key, TermSource("custom", "detected")).source,
                occurrences=counts[key],
            )
            for key, placeholder in sorted(assigned.items(), key=lambda item: item[1])
        )
        restore_map = {placeholder: canonical[key] for key, placeholder in assigned.items()}
        return "".join(pieces), redactions, restore_map


def _short_forms(name: str, kind: str) -> list[str]:
    """Shorter spellings of a name that a deck is likely to use bare.

    A company is named in full on the cover and by its head word everywhere
    else. A person is introduced with a role and referred to afterwards by
    surname alone.
    """
    words = name.split()
    out: list[str] = []
    if kind == "person":
        out.extend(
            word
            for word in words
            if len(word) >= _MIN_SHORT_FORM and word[:1].isupper()
        )
    else:
        head = strip_org_suffix(name).split()
        for count in range(len(head), 0, -1):
            candidate = " ".join(head[:count])
            if len(candidate) >= _MIN_SHORT_FORM:
                out.append(candidate)
    normalised_name = _normalise(name)
    return [form for form in out if _normalise(form) != normalised_name]


def _is_distinctive(phrase: str) -> bool:
    """Whether a phrase is safe to redact on sight.

    A phrase carries identifying weight of its own when at least one of its
    words is not ordinary English. Redacting a phrase made only of ordinary
    words — "Northern", "General", "Capital" — would strip meaning out of the
    payload wherever those words are used for their actual meaning.
    """
    return any(not _is_ordinary(word) for word in phrase.split())


def _first_line(text: str, pattern: re.Pattern[str]) -> int:
    match = pattern.search(text)
    if match is None:  # pragma: no cover - the caller has already matched
        return 1
    return text.count("\n", 0, match.start()) + 1


def _merge_map(terms: Mapping[str, TermSource]) -> dict[str, str]:
    """Map a shorter term onto a longer one it is a whole-word prefix of.

    A deck names the same company three ways: "Meridian Capital Partners LLP" on
    the cover, "Meridian Capital" in a table header, "Meridian" in a chart label.
    Without this they become ``[COMPANY_1]``, ``[COMPANY_3]`` and ``[COMPANY_7]``
    and the model can no longer see that a margin quoted under one is the same
    company as a margin quoted under another — which is the only reason it was
    shown the deck at all.

    The rule is deliberately narrow: whole-word prefix, same kind. It can still
    be wrong ("First Capital" and "First Capital Partners of Texas LLC" need not
    be related), and the failure is visible rather than silent — the analyst
    restores the real names and sees two different companies. The opposite error,
    splitting one company across three placeholders, produces a silent miss, and
    a miss is the worse of the two for a tool whose job is to find things.
    """
    by_kind: dict[str, list[tuple[list[str], str]]] = {}
    for term, source in terms.items():
        key = _normalise(term)
        by_kind.setdefault(source.kind, []).append((key.split(), key))

    merge: dict[str, str] = {}
    for entries in by_kind.values():
        entries.sort(key=lambda entry: (-len(entry[0]), entry[1]))
        for index, (words, key) in enumerate(entries):
            for longer_words, longer_key in entries[:index]:
                if longer_words[: len(words)] == words:
                    merge[key] = merge.get(longer_key, longer_key)
                    break
    return merge


def _scan_residuals(text: str, allowlist: frozenset[str]) -> tuple[Residual, ...]:
    """Find what the redaction did not clear.

    Deliberately over-eager, and structured so that the list stays short enough
    to actually read. The expensive part is the capitalised-phrase check, which
    behaves differently by line style: in a title-cased heading every word is
    capitalised for typographic reasons, so only words that are not ordinary
    English are suspicious; in a sentence, a capital mid-line is itself the
    signal.
    """
    found: dict[tuple[str, str], list[int]] = {}

    def record(value: str, reason: str, line_number: int) -> None:
        cleaned = value.strip(" \t .,;:!?()[]")
        if not cleaned or _normalise(cleaned) in allowlist:
            return
        if _normalise(cleaned) in FINANCE_VOCABULARY:
            return
        found.setdefault((cleaned, reason), []).append(line_number)

    for number, line in enumerate(text.splitlines(), start=1):
        bare = PLACEHOLDER_RE.sub(" ", line)

        for match in _AT_SIGN.finditer(bare):
            record(match.group(0), "contains an at-sign: an address or a handle", number)
        for match in _SCHEME.finditer(bare):
            record(match.group(0), "looks like a web address", number)
        for match in _LONG_DIGITS.finditer(bare):
            record(
                match.group(0),
                "nine or more digits with no decimal point: an identifier rather than a figure",
                number,
            )

        style = capitalisation_style(bare)
        for phrase, reason in _suspicious_phrases(bare, title_cased=style in ("title", "upper")):
            record(phrase, reason, number)

    return tuple(
        sorted(
            (
                Residual(
                    text=value,
                    reason=reason,
                    occurrences=len(lines),
                    first_line=min(lines),
                )
                for (value, reason), lines in found.items()
            ),
            key=lambda residual: (residual.first_line, residual.text),
        )
    )


def _suspicious_phrases(line: str, *, title_cased: bool) -> list[tuple[str, str]]:
    """Capitalised text on one line that may still identify someone.

    A residual is raised only where a capitalised run contains a word that is
    not ordinary English. The earlier version of this also reported any
    capitalised phrase sitting mid-line, on the reasoning that a capital in the
    middle of a sentence is where a name goes. On real material that fires
    constantly — deck text is fragments and pipe-separated table cells, not
    prose, so almost every word is line-initial or cell-initial — and it
    produced twenty residuals per deck, all of them ordinary words. A control
    that long is a control nobody reads, which is worse than not having it.

    So the scan catches *distinctive* identifiers, and the shortcoming is stated
    rather than papered over: a name made entirely of ordinary English words —
    "Northern Trust", "General Electric" — is invisible here unless it carries a
    legal suffix, a role, an honorific or a codename marker, all of which the
    detectors do catch. That residue is what the blocklist is for, and it is why
    the analyst is asked for one.
    """
    out: list[tuple[str, str]] = []
    run: list[str] = []

    def flush() -> None:
        uncommon = [word for word in run if not _is_ordinary(word)]
        if uncommon:
            if len(run) >= 2 and not title_cased:
                out.append(
                    (
                        " ".join(run),
                        "a capitalised phrase containing a word that is not ordinary English",
                    )
                )
            else:
                # A heading capitalises every word for typographic reasons, so
                # the phrase carries no signal and only the odd words do.
                for word in uncommon:
                    out.append((word, "a capitalised word that is not ordinary English"))
        run.clear()

    for match in _WORD.finditer(line):
        word = match.group(0)
        if word[:1].isupper():
            run.append(word)
        else:
            flush()
    flush()
    return out


def _is_ordinary(word: str) -> bool:
    """Whether a word carries no identifying weight on its own."""
    stripped = word.strip(".'’-&")
    for possessive in ("'s", "’s"):
        if stripped.lower().endswith(possessive):
            stripped = stripped[: -len(possessive)]
    if not stripped:
        return True
    if _normalise(stripped) in FINANCE_VOCABULARY:
        return True
    if stripped.isupper() and len(stripped) <= 5:
        # An acronym. The finance vocabulary above covers the common ones; the
        # rest are more often a metric than an identity, and a five-letter
        # ticker without its exchange prefix cannot be told from one.
        return True
    if is_common_word(stripped):
        return True
    # A hyphenated or slashed compound is ordinary when its parts are. Without
    # this, "Pre-tax" and "run-rate" are residuals on every deck — they are not
    # in any word list as written — and a residual list that always has the same
    # two entries in it teaches people to clear it without reading.
    parts = [part for part in re.split(r"[\-/]", stripped) if part]
    return len(parts) > 1 and all(is_common_word(part) for part in parts)
