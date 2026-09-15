"""Text, number and date analysis shared by the typography deriver and rules.

The deriver and the rule that enforces what it derived must agree exactly. If
the deriver decides a column has two decimal places by one method and the rule
checks it by another, the tool reports defects on the deck it learned from. So
both call the functions here and neither implements its own parsing.

All of it is pure: strings in, measurements out, no deck knowledge.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------------------
# Quotes and whitespace
# --------------------------------------------------------------------------------------

CURLY_SINGLE: Final[str] = "‘’"
CURLY_DOUBLE: Final[str] = "“”"
STRAIGHT_SINGLE: Final[str] = "'"
STRAIGHT_DOUBLE: Final[str] = '"'

#: A straight apostrophe that is genuinely a foot or inch mark, or a prime in a
#: ticker, is not a typographic defect.
_MEASURE_CONTEXT: Final[re.Pattern[str]] = re.compile(r"\d\s*['\"]")


@dataclass(frozen=True, slots=True)
class QuoteCensus:
    """Counts of quote glyphs in a body of text."""

    curly: int
    straight: int

    @property
    def total(self) -> int:
        return self.curly + self.straight

    @property
    def dominant(self) -> str | None:
        if self.total == 0:
            return None
        return "curly" if self.curly >= self.straight else "straight"


def quote_census(text: str) -> QuoteCensus:
    """Count curly and straight quote marks, ignoring measurement marks."""
    cleaned = _MEASURE_CONTEXT.sub("", text)
    curly = sum(cleaned.count(ch) for ch in CURLY_SINGLE + CURLY_DOUBLE)
    straight = cleaned.count(STRAIGHT_SINGLE) + cleaned.count(STRAIGHT_DOUBLE)
    return QuoteCensus(curly=curly, straight=straight)


@dataclass(frozen=True, slots=True)
class WhitespaceDefect:
    """One whitespace or spacing defect, with enough context to be actionable."""

    kind: str
    excerpt: str
    position: int


#: Space before one of these is always wrong in English typesetting.
_SPACE_BEFORE_PUNCTUATION: Final[re.Pattern[str]] = re.compile(r"\s+([,.;:!?])")
_DOUBLE_SPACE: Final[re.Pattern[str]] = re.compile(r"[^\S\n]{2,}")
_TRAILING_SPACE: Final[re.Pattern[str]] = re.compile(r"[^\S\n]+$", re.MULTILINE)
_SPACE_AFTER_OPEN: Final[re.Pattern[str]] = re.compile(r"([([])\s+")
_SPACE_BEFORE_CLOSE: Final[re.Pattern[str]] = re.compile(r"\s+([)\]])")


def whitespace_defects(text: str) -> list[WhitespaceDefect]:
    """Find spacing defects TY-002 reports.

    Deliberately does not flag a single space before an em dash or an ellipsis,
    both of which are legitimate house style in prose.
    """
    out: list[WhitespaceDefect] = []
    for pattern, kind in (
        (_DOUBLE_SPACE, "double space"),
        (_TRAILING_SPACE, "trailing whitespace"),
        (_SPACE_BEFORE_PUNCTUATION, "space before punctuation"),
        (_SPACE_AFTER_OPEN, "space after an opening bracket"),
        (_SPACE_BEFORE_CLOSE, "space before a closing bracket"),
    ):
        for match in pattern.finditer(text):
            out.append(
                WhitespaceDefect(
                    kind=kind,
                    excerpt=_excerpt(text, match.start(), match.end()),
                    position=match.start(),
                )
            )
    if " " in text and " " in text:
        index = text.index(" ")
        out.append(
            WhitespaceDefect(
                kind="mixed non-breaking and ordinary spaces",
                excerpt=_excerpt(text, index, index + 1),
                position=index,
            )
        )
    return out


def _excerpt(text: str, start: int, end: int, window: int = 18) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    fragment = text[left:right].replace("\n", " ").replace(" ", "<nbsp>")
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(text) else ""
    return f"{prefix}{fragment}{suffix}"


# --------------------------------------------------------------------------------------
# Capitalisation
# --------------------------------------------------------------------------------------

#: Words that stay lower case inside a title-cased heading.
_MINOR_WORDS: Final[frozenset[str]] = frozenset(
    {
        "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
        "nor", "of", "on", "onto", "or", "over", "per", "so", "the", "to", "up",
        "via", "with", "yet", "vs", "v",
    }
)

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def capitalisation_style(text: str) -> str | None:
    """Classify a heading as ``sentence``, ``title`` or ``upper``.

    Returns None when the heading is too short to tell, which matters: a
    two-word heading is consistent with either convention and must not be
    allowed to vote.

    Words that are capitalised in both conventions -- proper nouns, acronyms,
    invented codenames -- are excluded from the decision. Without that, every
    heading naming a company reads as title case.
    """
    words = _WORD_RE.findall(text.strip())
    if len(words) < 3:
        return None

    if all(word.isupper() for word in words if len(word) > 1):
        return "upper"

    decidable: list[str] = []
    for index, word in enumerate(words):
        if index == 0:
            continue  # the first word is capitalised either way
        if word.isupper():
            continue  # an acronym
        if word.lower() in _MINOR_WORDS:
            continue  # lower case in both conventions
        decidable.append(word)

    if not decidable:
        return None

    capitalised = sum(1 for word in decidable if word[:1].isupper())
    share = capitalised / len(decidable)
    if share >= 0.75:
        return "title"
    if share <= 0.25:
        return "sentence"
    return None


def looks_like_proper_noun(word: str) -> bool:
    """Whether a capitalised word is plausibly a name rather than a style choice."""
    return word[:1].isupper() and not word.isupper() and len(word) > 1


# --------------------------------------------------------------------------------------
# Bullets
# --------------------------------------------------------------------------------------

_TERMINAL_MAP: Final[dict[str, str]] = {".": "period", ";": "semicolon"}


def bullet_terminal(text: str) -> str:
    """Classify how a bullet ends: ``none``, ``period`` or ``semicolon``.

    A bullet ending in a question mark, an exclamation mark or a closing bracket
    is reported as ``none``: those are content, not a punctuation convention.
    """
    stripped = text.strip()
    if not stripped:
        return "none"
    if stripped.endswith("..."):
        return "none"
    return _TERMINAL_MAP.get(stripped[-1], "none")


# --------------------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------------------

_NUMERIC_CORE: Final[re.Pattern[str]] = re.compile(
    r"""^
    (?P<open>\()?                         # parenthesised negative
    \s*
    (?P<currency>US\$|U\.S\.\$|USD|EUR|GBP|JPY|CHF|\$|£|€|¥)?
    \s*
    (?P<minus>-|−)?                  # hyphen-minus or true minus sign
    \s*
    (?P<int>\d{1,3}(?:[,  ]\d{3})*|\d+)
    (?:\.(?P<frac>\d+))?
    \s*
    (?P<suffix>%|x|bp|bps|p\.a\.|m|bn|k|mm)?
    \s*
    (?P<close>\))?
    $""",
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NumberReading:
    """A parsed numeric string."""

    raw: str
    value: float
    decimals: int
    thousands_separator: str | None
    negative_style: str | None
    currency: str | None
    suffix: str | None

    @property
    def is_negative(self) -> bool:
        return self.negative_style is not None


def parse_number(text: str) -> NumberReading | None:
    """Parse a cell value, returning None when it is not a number.

    Recognises parenthesised negatives, thousands separators in comma, space and
    non-breaking-space forms, currency prefixes and the multiple/percentage/basis
    point suffixes that appear in banking tables. Anything else is not a number,
    and saying so is better than coercing ``n.a.`` to zero.
    """
    stripped = text.strip()
    if not stripped:
        return None
    match = _NUMERIC_CORE.match(stripped)
    if match is None:
        return None

    groups = match.groupdict()
    integer_part = groups["int"]
    separator: str | None = None
    for candidate in (",", " ", " "):
        if candidate in integer_part:
            separator = candidate
            break

    digits = re.sub(r"[,  ]", "", integer_part)
    fraction = groups["frac"] or ""
    magnitude = float(f"{digits}.{fraction}") if fraction else float(digits)

    negative_style: str | None = None
    if groups["open"] and groups["close"]:
        negative_style = "parentheses"
    elif groups["minus"]:
        negative_style = "minus"
    elif groups["open"] or groups["close"]:
        # An unbalanced bracket is not a negative number; it is a defect in the
        # cell, and reporting it as a number with an odd style would hide that.
        return None

    return NumberReading(
        raw=stripped,
        value=-magnitude if negative_style else magnitude,
        decimals=len(fraction),
        thousands_separator=separator,
        negative_style=negative_style,
        currency=groups["currency"],
        suffix=groups["suffix"],
    )


def is_numeric(text: str) -> bool:
    return parse_number(text) is not None


#: Values that legitimately appear in a numeric column and must not be read as
#: a formatting deviation.
NON_NUMERIC_PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    {
        "n.a.", "n/a", "na", "nm", "n.m.", "nil", "-", "–", "—",
        "tbd", "", "--",
    }
)


def is_numeric_placeholder(text: str) -> bool:
    return text.strip().casefold() in NON_NUMERIC_PLACEHOLDERS


# --------------------------------------------------------------------------------------
# Currency and units
# --------------------------------------------------------------------------------------

_CURRENCY_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"(US\$|U\.S\.\$|USD|EUR|GBP|JPY|\$|£|€|¥)\s?(?=[\d.])",
    re.IGNORECASE,
)


def currency_tokens(text: str) -> list[str]:
    """Every currency marker immediately preceding a figure."""
    return [match.group(1) for match in _CURRENCY_TOKEN.finditer(text)]


def normalise_currency(token: str) -> str:
    """Fold a currency marker to a comparable form.

    ``US$``, ``U.S.$`` and ``USD`` all denote the same currency but are not
    interchangeable in a house style, so the normalised form keeps them distinct
    while collapsing case and spacing.
    """
    return token.replace(" ", "").replace(".", "").upper()


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------

#: The patterns TieOut will try, in order. A date that matches none of them is
#: reported as unrecognised rather than guessed at.
DATE_FORMATS: Final[tuple[str, ...]] = (
    "%d-%B-%Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%B %Y",
    "%b %Y",
    "%d-%B-%y",
    "%d/%m/%y",
    "%m/%d/%y",
)

_DATE_CANDIDATE: Final[re.Pattern[str]] = re.compile(
    r"""(
        \d{1,2}[-/. ](?:\d{1,2}|[A-Za-z]{3,9})[-/. ]\d{2,4}
      | [A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}
      | \d{4}-\d{2}-\d{2}
      | [A-Za-z]{3,9}\s+\d{4}
    )""",
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class DateReading:
    raw: str
    format: str


def find_dates(text: str) -> list[DateReading]:
    """Extract date-looking substrings and the format each one parses under.

    ``%d/%m/%Y`` and ``%m/%d/%Y`` are genuinely ambiguous for a day below 13.
    The first matching format in :data:`DATE_FORMATS` wins, which makes the
    result deterministic; the ambiguity is called out in the README rather than
    resolved by guessing at the author's locale.
    """
    out: list[DateReading] = []
    for match in _DATE_CANDIDATE.finditer(text):
        candidate = match.group(1).strip()
        for pattern in DATE_FORMATS:
            try:
                datetime.strptime(candidate, pattern)
            except ValueError:
                continue
            out.append(DateReading(raw=candidate, format=pattern))
            break
    return out


# --------------------------------------------------------------------------------------
# Terminology
# --------------------------------------------------------------------------------------

#: Digits are admitted after the first letter so that a fiscal label stays one
#: token. Without them "FY26E REVENUE" tokenises as "FY", "E", "REVENUE", the
#: run breaks at the digits, and the deck acquires a phantom term "E REVENUE".
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9'’&.-]*")
_SENTENCE_SPLIT: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+|\n+")

#: What may sit between two capitalised words without breaking the phrase.
_JOINABLE: Final[re.Pattern[str]] = re.compile(r"[ \u00a0-]*")


def capitalised_ngrams(
    text: str, max_length: int = 4
) -> list[tuple[str, bool]]:
    """Extract runs of capitalised words, with whether each is sentence-initial.

    The flag matters. "Revenue growth has slowed" and "revenue growth has
    slowed" differ only in sentence position, and offering that pair to the user
    as a terminology question is noise -- section 8.3 says so explicitly.
    """
    out: list[tuple[str, bool]] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        tokens = [
            (match.group(0), match.start())
            for match in _TOKEN_RE.finditer(sentence)
        ]
        if not tokens:
            continue
        first_position = tokens[0][1]
        run: list[tuple[str, int]] = []
        previous_end = -1
        for token, position in tokens:
            if not token[:1].isupper():
                _emit_runs(run, first_position, max_length, out)
                run = []
                previous_end = position + len(token)
                continue
            # Two capitalised words only form a phrase when nothing but spacing
            # separates them. Without this, "Source: Company management" yields
            # the phantom term "Source Company", and a deck whose every footnote
            # begins "Source:" acquires a canon entry for it.
            if run and not _JOINABLE.fullmatch(sentence[previous_end:position]):
                _emit_runs(run, first_position, max_length, out)
                run = []
            run.append((token, position))
            previous_end = position + len(token)
        _emit_runs(run, first_position, max_length, out)
    return out


def _emit_runs(
    run: list[tuple[str, int]],
    first_position: int,
    max_length: int,
    out: list[tuple[str, bool]],
) -> None:
    if not run:
        return
    for length in range(1, min(max_length, len(run)) + 1):
        for start in range(0, len(run) - length + 1):
            window = run[start : start + length]
            phrase = " ".join(token for token, _ in window)
            sentence_initial = window[0][1] == first_position
            out.append((phrase, sentence_initial))


def clean_term(phrase: str) -> str:
    """Trim a captured phrase to the term itself.

    The token pattern deliberately admits internal hyphens, apostrophes and full
    stops so that "U.S.", "Coca-Cola" and "Moody's" survive intact. The cost is
    that a date such as "14-September-2026" yields the fragment "September-", and
    a possessive yields "Board's" as if it were a distinct term. Both are trimmed
    here rather than in the token pattern, which would break the forms above.
    """
    words: list[str] = []
    for word in phrase.split():
        word = word.strip("-.\u2013\u2014")
        for suffix in ("'s", "\u2019s", "'", "\u2019"):
            if word.endswith(suffix) and len(word) > len(suffix) + 1:
                word = word[: -len(suffix)]
                break
        if word:
            words.append(word)
    return " ".join(words)


def canon_key(phrase: str) -> str:
    """The normalisation key that groups surface forms of the same term.

    Lower case, punctuation stripped, whitespace collapsed. ``Toyota GAZOO
    Racing`` and ``Toyota Gazoo Racing`` share a key; ``Toyota Racing`` does not.
    """
    folded = unicodedata.normalize("NFKD", clean_term(phrase)).casefold()
    folded = re.sub(r"[^\w\s]", "", folded)
    return re.sub(r"\s+", " ", folded).strip()


# --------------------------------------------------------------------------------------
# Spell checking support
# --------------------------------------------------------------------------------------

_SPELL_TOKEN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z'’]*")
_TICKER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{1,5}(?:[.:][A-Z]{1,3})?$")


def spell_tokens(text: str) -> list[str]:
    """Words worth spell checking.

    Skips tickers, short all-caps acronyms, anything containing a digit and
    single letters, per TY-009's specification. These are the categories that
    generate essentially all of a naive spell checker's false positives on
    banking material.
    """
    out: list[str] = []
    for match in _SPELL_TOKEN.finditer(text):
        token = match.group(0)
        if len(token) < 3:
            continue
        if token.isupper() and 2 <= len(token) <= 5:
            continue
        if _TICKER_RE.match(token):
            continue
        out.append(token)
    return out


def spell_variants(token: str) -> tuple[str, ...]:
    """Forms of a token to test against a dictionary.

    A dictionary of base words will not contain a possessive or a capitalised
    sentence-initial form, and rejecting those would make the rule useless.
    """
    lowered = token.casefold().replace("’", "'")
    forms = {lowered}
    for suffix in ("'s", "s'"):
        if lowered.endswith(suffix):
            forms.add(lowered[: -len(suffix)])
    if lowered.endswith("'"):
        forms.add(lowered[:-1])
    return tuple(sorted(forms))


# --------------------------------------------------------------------------------------
# Bundled dictionary
# --------------------------------------------------------------------------------------

_WORDLIST_PATH: Final[Path] = Path(__file__).with_name("data") / "wordlist.txt.gz"
_WORDLIST_CACHE: frozenset[str] | None = None


def load_wordlist() -> frozenset[str]:
    """The bundled English and corporate-finance dictionary, lowercased.

    Cached at module level: it is roughly 38,000 words, and re-reading it per
    shape would dominate the runtime of a spell check over a long deck.

    Shared by TY-009 and by the terminology deriver, which uses it for the
    opposite purpose -- deciding that a capitalised word is ordinary English and
    therefore *not* a client term worth a canon entry.
    """
    global _WORDLIST_CACHE
    if _WORDLIST_CACHE is None:
        try:
            with gzip.open(_WORDLIST_PATH, "rt", encoding="utf-8") as handle:
                _WORDLIST_CACHE = frozenset(
                    line.strip().casefold() for line in handle if line.strip()
                )
        except OSError:
            _WORDLIST_CACHE = frozenset()
    return _WORDLIST_CACHE


def is_common_word(word: str) -> bool:
    """Whether a word is ordinary English rather than a name or a coinage."""
    return word.casefold().strip("'\u2019") in load_wordlist()
