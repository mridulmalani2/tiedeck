"""Pattern detectors for identifying information.

These are the things that identify a client without naming them: the banker's
email address in a footer, the deal-room URL, a local file path with a project
codename in it, a ticker, an address, a phone number. A blocklist cannot catch
them because nobody knows them in advance.

Every detector is a regular expression over plain text and nothing more. That is
deliberate: the redaction layer is the security boundary of this package, and a
boundary you cannot read in one sitting is not one you can trust. There is no
model here, no heuristic scoring and no network.

Two properties matter for everything below:

* **Numbers are never matched.** The whole purpose of the review layer is to let
  a model see that a margin is 15.6% in one place and 15.8% in another. A
  detector that swallowed figures would defeat it. The only digits these
  patterns touch are ones embedded in a contact detail, a path or an address.
* **Detectors over-match rather than under-match.** A false positive costs one
  placeholder in a payload; a false negative sends a client's name to a third
  party. Where the two trade off, these err loudly in the safe direction, and
  the caller's review step is where over-matching gets corrected.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DETECTORS",
    "Detector",
    "Span",
    "detect",
    "iter_spans",
]


@dataclass(frozen=True, slots=True)
class Span:
    """One detected stretch of identifying text.

    ``text`` is the exact substring to redact, which is not always the whole
    match: a company-suffix detector matches "Meridian Capital Partners LLP" but
    a role detector matches "CFO, Jane Okafor" and only wants the name.
    """

    kind: str
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Detector:
    """A named pattern and the capture group that holds the text to redact.

    ``trim_trailing`` strips sentence punctuation from the end of a match. A URL
    or a path never ends in a full stop but a sentence containing one does, so
    without it the harvested term is "www.example.com/deals." and matches the
    same URL nowhere else. Company names are excluded from this, because "S.p.A."
    and "Inc." genuinely end in a period.
    """

    kind: str
    pattern: re.Pattern[str]
    group: int = 0
    trim_trailing: str = ""

    def scan(self, text: str) -> Iterator[Span]:
        for match in self.pattern.finditer(text):
            captured = match.group(self.group)
            if not captured:
                continue
            start = match.start(self.group)
            stripped = captured.strip()
            start += len(captured) - len(captured.lstrip())
            if self.trim_trailing:
                stripped = stripped.rstrip(self.trim_trailing)
            if not stripped:
                continue
            yield Span(
                kind=self.kind,
                text=stripped,
                start=start,
                end=start + len(stripped),
            )


#: Horizontal whitespace only. Every multi-word pattern below uses this rather
#: than ``\s``: a name or company pattern allowed to cross a newline stitches the
#: end of one line to the start of the next, and the resulting term matches
#: neither of the two real things it spans.
_H: Final[str] = r"[ \t\u00a0]"


# --------------------------------------------------------------------------- #
# Contact details and locators
# --------------------------------------------------------------------------- #

#: Deliberately permissive on the local part: a footer address is as likely to be
#: ``j.okafor@`` or ``project.meridian@`` as a plain name.
_EMAIL: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

#: A URL, or a bare host that carries a recognisable public suffix. The bare-host
#: half is what catches "meridiancapital.com" in a footer, which is the form a
#: deck actually uses.
_URL: Final[re.Pattern[str]] = re.compile(
    r'''(?:
          (?:https?|ftp|sftp|s3|file)://[^\s<>"')\]]+
        | www\.[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?:/[^\s<>"')\]]*)?
        | \b[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*
          \.(?:com|net|org|io|ai|co|co\.uk|de|fr|ch|sg|hk|jp|cn|in|au|ca|nl|se|no|dk|es|it|
             biz|info|us|eu|gov|edu|bank|finance|capital|group|fund|partners)
          (?:/[^\s<>"')\]]*)?\b
      )''',
    re.VERBOSE | re.IGNORECASE,
)

#: International and domestic shapes, requiring either a leading ``+`` or enough
#: separators that it cannot be mistaken for a financial figure. A bare run of
#: digits is left to the residual scan rather than guessed at here, because
#: "1 908 000" is a revenue line and "020 7123 4567" is a desk number and only
#: the punctuation tells them apart.
_PHONE: Final[re.Pattern[str]] = re.compile(
    r"""(?<![\d.,])(?:
          \+\d{1,3}[\s.\-]?(?:\(\d{1,4}\)[\s.\-]?)?\d{1,4}(?:[\s.\-]\d{2,5}){1,4}
        | \(\d{2,5}\)[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}
        | \b0\d{1,4}[\s.\-]\d{3,4}[\s.\-]?\d{3,4}\b
        | \b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b
      )(?!\d)(?![,.]\d)""",
    re.VERBOSE,
)

#: A local path. These leak internal structure and project codenames whenever a
#: chart keeps its source workbook or a footer quotes a shared drive.
_WINDOWS_PATH: Final[re.Pattern[str]] = re.compile(
    r'''(?:[A-Za-z]:\\|\\\\)[^\s<>"'|?*]+'''
)
_POSIX_PATH: Final[re.Pattern[str]] = re.compile(
    r'''(?:/(?:Users|home|Volumes|mnt|media|srv)/|~/)[^\s<>"'|?*]+'''
)

#: ``NYSE: MRDN``, ``LSE:ABC``, ``(NASDAQ: XYZA)``. The exchange prefix is what
#: makes this safe to match: a bare three-letter capital is far more often
#: EPS or IRR than a ticker.
_TICKER: Final[re.Pattern[str]] = re.compile(
    r"\b(?:NYSE|NASDAQ|LSE|LON|AMEX|TSX|ASX|HKEX|SGX|SEHK|EPA|ETR|FRA|BIT|BME|OTC|XETRA)"
    r"\s*[:.]\s*([A-Z]{1,6}(?:\.[A-Z]{1,2})?)"
)

#: A social handle. Cheap to catch and unambiguous.
_HANDLE: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z0-9._%+\-])@[A-Za-z][A-Za-z0-9_]{2,}\b")


# --------------------------------------------------------------------------- #
# Organisations
# --------------------------------------------------------------------------- #

#: Legal and quasi-legal suffixes. ``Holdings``, ``Group``, ``Capital`` and their
#: neighbours are not legal forms, but in a banking deck a capitalised phrase
#: ending in one of them is a company name essentially every time.
_ORG_SUFFIX: Final[str] = r"""(?:
      Inc|Incorporated|Corp|Corporation|Company|Co
    | Ltd|Limited|LLC|L\.L\.C|LLP|L\.L\.P|LP|PLC|P\.L\.C
    | GmbH|AG|SE|SA|S\.A|SAS|SARL|NV|N\.V|BV|B\.V|AB|AS|ApS|Oy|SpA|S\.p\.A|Srl|S\.r\.l
    | Pty|Pte|Bhd|Sdn|KK|K\.K|Kabushiki
    | Holdings|Holding|Group|Capital|Partners|Ventures|Advisors|Advisers|Associates
    | Bancorp|Bankshares|Securities|Asset\ Management|Investments|Industries
    | Technologies|Solutions|Systems|Enterprises|Trust|Fund|Foundation
  )"""

#: Words that must not start a company name. Without this, "CFO of Meridian
#: Capital Partners LLP" is harvested whole — which reads as a safe redaction and
#: is not, because the harvested term is then too long to match "Meridian Capital
#: Partners LLP" anywhere else in the deck. Blocking the position simply makes
#: the scan resume one word later and find the real name.
_NOT_ORG_LEAD: Final[str] = r"""(?!(?:
      CEO|CFO|COO|CTO|CIO|CRO|CMO|CHRO|CPO|VP|SVP|EVP|AVP|MD
    | Chief|Chairman|Chairwoman|Chairperson|Chair|Director|Partner|President
    | Founder|Owner|Principal|Associate|Analyst|Controller|Treasurer|Secretary
    | Head|Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Professor|Sir|Dame|Lord|Lady|Hon
    | The|This|That|These|Those|Our|Your|Their|Its|His|Her|An?|And|Or|But|For|With
    | Source|Sources|Note|Notes|Total|Revenue|EBITDA|EBIT|Margin|Growth
    | Summary|Overview|Agenda|Appendix|Exhibit|Illustrative|Selected|Preliminary
  )\s)"""

#: One to five capitalised words followed by a suffix. Lowercase particles and
#: ``&`` may join them, in runs, because real names have them: "Bank of the West
#: Holdings", "Rossi & Figli S.p.A".
#: A capitalised word, or a short digit-led one so that "3M Company" still
#: matches. Without the length bound on the digit-led form, "14-September-2026."
#: counts as a word and "14-September-2026. Capital" is harvested as a company —
#: a term that matches nothing else in the deck and reads as a redaction.
#: A full stop is part of a word only in an abbreviation, where a letter or digit
#: follows it: "S.p.A", "U.S". Allowing it freely let a word run through a
#: sentence boundary, and "…as at 14-September-2026. Capital in US$ millions"
#: was harvested as the company "September-2026. Capital".
_ORG_TAIL: Final[str] = r"(?:[A-Za-z0-9'’\-]|\.(?=[A-Za-z0-9]))*"
_ORG_WORD: Final[str] = r"(?:[A-Z]" + _ORG_TAIL + r"|\d{1,2}[A-Za-z]" + _ORG_TAIL + r")"
_ORG_JOIN: Final[str] = (
    r"(?:" + _H + r"+(?:of|the|for|a|an|at|in|on|de|del|della|la|le|les|du|des"
    r"|van|von|der|den|di|da|do|dos|el|al|&))"
    r"{0,3}" + _H + r"+"
)
_ORGANISATION: Final[re.Pattern[str]] = re.compile(
    r"\b" + _NOT_ORG_LEAD + r"("
    + _ORG_WORD
    + r"(?:" + _ORG_JOIN + _ORG_WORD + r"){0,4}"
    + _H + r"+"
    + _ORG_SUFFIX
    + r"\.?)\b",
    re.VERBOSE,
)


#: ``Project Meridian``, ``Operation Falcon``, ``Deal Atlas``. A codename is the
#: single most identifying token in a live deal deck and the one most likely to
#: be left in a footer.
_CODENAME: Final[re.Pattern[str]] = re.compile(
    r"\b(?i:project|operation|deal|transaction|initiative|target|codename)" + _H + r"+"
    r"([A-Z][A-Za-z0-9'’\-]{2,}(?:" + _H + r"+[A-Z][A-Za-z0-9'’\-]{2,})?)\b"
)


# --------------------------------------------------------------------------- #
# People
# --------------------------------------------------------------------------- #

_ROLES: Final[str] = r"""(?:
      CEO|CFO|COO|CTO|CIO|CRO|CMO|CHRO|CPO
    | Chief\ (?:Executive|Financial|Operating|Technology|Information|Risk|Marketing)
      (?:\ Officer)?
    | Chairman|Chairwoman|Chairperson|Chair
    | (?:Managing\ |Executive\ |Senior\ |Non-Executive\ )?Director
    | (?:Managing\ |Senior\ |General\ )?Partner
    | President|Vice\ President|VP|SVP|EVP|AVP
    | Founder|Co-Founder|Owner|Proprietor
    | Head\ of\ [A-Z][A-Za-z]+(?:\ [A-Z][A-Za-z]+)?
    | Principal|Associate|Analyst|Controller|Treasurer|Secretary
  )"""

#: Lowercase particles that sit inside a surname.
_NAME_PARTICLE: Final[str] = (
    r"(?:de|del|della|di|da|do|dos|du|des|la|le|les|van|von|der|den|ter"
    r"|bin|binti|ibn|al|el|st|af|av|mac|mc)"
)

#: One more word of a name: any number of particles, then a capitalised word. By
#: requiring the run to end on the capitalised word, a capture can never finish
#: on a dangling "de".
_NAME_STEP: Final[str] = (
    r"(?:(?:" + _H + r"+" + _NAME_PARTICLE + r"){0,3}" + _H + r"+[A-Z][a-z'’\-]{1,})"
)

#: A person's name spelled with a capitalised forename and surname.
_NAME: Final[str] = r"[A-Z][a-z'’\-]{1,}" + _NAME_STEP + r"{1,2}"

#: The looser form for an attributed line, where an initial ("A. Patel") is as
#: likely as a spelled-out forename.
_ATTRIBUTED_STEP: Final[str] = (
    r"(?:(?:" + _H + r"+" + _NAME_PARTICLE + r"){0,3}" + _H + r"+[A-Z][A-Za-z'’.\-]*)"
)

#: ``CFO, Jane Okafor`` and ``Jane Okafor, CFO``. The role is what distinguishes a
#: person from any other capitalised pair, which is why a bare name is left to
#: the residual scan instead of being matched here.
_ROLE_GAP: Final[str] = r"(?:" + _H + r"|[,:;—–\-])+"
_NAME_AFTER_ROLE: Final[re.Pattern[str]] = re.compile(
    _ROLES + _ROLE_GAP + r"(" + _NAME + r")\b", re.VERBOSE
)
_NAME_BEFORE_ROLE: Final[re.Pattern[str]] = re.compile(
    r"\b(" + _NAME + r")" + _ROLE_GAP + _ROLES, re.VERBOSE
)

#: ``Mr Patel``, ``Dr. Anna Weiss``. An honorific is as good as a role.
_TITLED_NAME: Final[re.Pattern[str]] = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Professor|Sir|Dame|Lord|Lady|Hon)\.?" + _H + r"+"
    r"([A-Z][a-z'’\-]{1,}" + _NAME_STEP + r"{0,2})\b"
)

#: ``Prepared by Jane Okafor``, ``Contact: A. Patel``, ``Presented by ...``.
_ATTRIBUTED_NAME: Final[re.Pattern[str]] = re.compile(
    r"\b(?i:prepared|presented|compiled|reviewed|approved|authored|written|contact"
    r"|attention|attn)\b(?:" + _H + r"+(?i:by|for|to))?(?:" + _H + r"|[,:])+"
    r"([A-Z][A-Za-z'’.\-]*" + _ATTRIBUTED_STEP + r"{0,2})"
)


# --------------------------------------------------------------------------- #
# Places
# --------------------------------------------------------------------------- #

_STREET_TYPE: Final[str] = r"""(?:
      Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Boulevard|Blvd|Drive|Dr
    | Square|Sq|Place|Pl|Court|Ct|Terrace|Way|Wharf|Quay|Plaza|Tower|House
  )"""

_ADDRESS: Final[re.Pattern[str]] = re.compile(
    r"\b(" + r"\d{1,5}[A-Za-z]?" + _H + r"+"
    r"(?:[A-Z][A-Za-z'’\-]*" + _H + r"+){1,4}"
    + _STREET_TYPE
    + r"\b\.?)",
    re.VERBOSE,
)

#: UK postcodes and US ZIP+4. Both pin a location precisely enough to identify an
#: office, and neither can be confused with a financial figure.
_POSTCODE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}|\d{5}-\d{4})\b"
)


#: Sentence punctuation that is never part of a locator.
_TRAIL: Final[str] = ".,;:!?)]}>'\"«»"


#: Ordered most specific first. Overlaps are resolved by span length in
#: :func:`detect`, so the order here is documentation rather than precedence.
DETECTORS: Final[tuple[Detector, ...]] = (
    Detector("email", _EMAIL, trim_trailing=_TRAIL),
    Detector("url", _URL, trim_trailing=_TRAIL),
    Detector("path", _WINDOWS_PATH, trim_trailing=_TRAIL),
    Detector("path", _POSIX_PATH, trim_trailing=_TRAIL),
    Detector("phone", _PHONE),
    Detector("ticker", _TICKER, group=1),
    Detector("handle", _HANDLE, trim_trailing=_TRAIL),
    Detector("company", _ORGANISATION, group=1),
    Detector("codename", _CODENAME, group=1),
    Detector("person", _NAME_AFTER_ROLE, group=1),
    Detector("person", _NAME_BEFORE_ROLE, group=1),
    Detector("person", _TITLED_NAME, group=1),
    Detector("person", _ATTRIBUTED_NAME, group=1),
    Detector("address", _ADDRESS, group=1),
    Detector("postcode", _POSTCODE),
)


#: The organisation suffix on its own, anchored at the end of a name, so a
#: harvested "Calderwood Holdings" can be reduced to "Calderwood".
_ORG_SUFFIX_ONLY: Final[re.Pattern[str]] = re.compile(
    r"[\s,]+" + _ORG_SUFFIX + r"\.?\s*$", re.VERBOSE
)


def strip_org_suffix(name: str) -> str:
    """A company name without its legal or quasi-legal suffix.

    The suffix is what lets a detector recognise the name at all; the deck then
    uses the bare head in every table header and chart label, where no detector
    fires. Returning the head is how those get redacted too.
    """
    stripped = _ORG_SUFFIX_ONLY.sub("", name).strip(" ,.")
    return stripped if stripped else name


def iter_spans(text: str, detectors: tuple[Detector, ...] = DETECTORS) -> Iterator[Span]:
    """Every span every detector finds, overlaps included."""
    for detector in detectors:
        yield from detector.scan(text)


def detect(text: str, detectors: tuple[Detector, ...] = DETECTORS) -> list[Span]:
    """Detected spans with overlaps resolved, in order of appearance.

    When two detectors overlap the longer span wins, and on a tie the one from
    the earlier detector does. "Meridian Capital Partners LLP" is redacted whole
    rather than leaving "Meridian Capital" behind as a fragment, which is the
    failure mode that matters: a partial redaction reads as safe and is not.
    """
    spans = sorted(
        iter_spans(text, detectors),
        key=lambda span: (span.start, -(span.end - span.start)),
    )
    kept: list[Span] = []
    reach = -1
    for span in spans:
        if span.start < reach:
            # Overlapping a span already kept. Keep the longer of the two.
            if span.end > reach and kept and span.start <= kept[-1].start:
                kept[-1] = span
                reach = span.end
            continue
        kept.append(span)
        reach = span.end
    return kept
