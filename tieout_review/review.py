"""Orchestration: extract, redact, hold, send, parse.

The shape of this module is the safety property. There is no function that takes
a deck and returns findings, because such a function would have to decide on its
own that a payload was safe to transmit. Instead:

    prepared = prepare(deck, profile, forbidden=...)      # offline, always
    prepared.plan.is_clear                                # or show the residuals
    outcome = send(prepared.approve(digest), client)      # refuses otherwise

:func:`prepare` never touches a network and never needs a key, so an analyst can
run it — via ``tieout-review redact`` — to see exactly what would leave the
machine before deciding whether to allow it. :func:`send` refuses a payload with
residuals outstanding, and independently re-verifies that every literal term is
absent from the outgoing text. Two people would have to be wrong for a client
name to leave the building: whoever wrote the detectors, and whoever approved a
residual list they had read.

The approval names a digest rather than being a bare ``True``, because over HTTP
"approved" arrives in a request after the one that displayed the list, and the
server had no way to tell the two apart from an approval of something else
entirely. :mod:`tieout_review.attest` says what the digest covers and why it has
to cover the payload text as well as the list. Every send is recorded by
:mod:`tieout_review.outbound` before it leaves, and a send that cannot be
recorded is not made.

Findings arrive in a ``semantic`` category, which
:data:`tieout.rules.base.NON_GATING_CATEGORIES` excludes from ``--fail-on``. A
probabilistic finding must not be able to change what an existing deterministic
gate does; someone who wants it to can ask for it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from tieout.model.deck import DeckModel
from tieout.profile.schema import Confidence, Profile, Severity
from tieout.rules.base import Finding
from tieout_review.attest import residual_digest, short_digest, text_digest
from tieout_review.extract import DeckPayload, extract
from tieout_review.outbound import OutboundRecord
from tieout_review.outbound import record as record_outbound
from tieout_review.redact import Redacted, Redactor, TermSource
from tieout_review.terms import assemble

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tieout_review.client import ReviewClient

__all__ = [
    "SEMANTIC_CATEGORY",
    "SEMANTIC_RULES",
    "ApprovalMismatch",
    "Prepared",
    "RedactionFailed",
    "RedactionHeld",
    "ResponseError",
    "ReviewOutcome",
    "SemanticRule",
    "build_response_schema",
    "prepare",
    "send",
]

SEMANTIC_CATEGORY: Final[str] = "semantic"


class RedactionHeld(RuntimeError):
    """Transmission was refused. The residual list is the reason."""

    def __init__(self, prepared: Prepared) -> None:
        self.prepared = prepared
        count = len(prepared.plan.residuals)
        if prepared.approved_digest is None:
            reason = "have not been reviewed"
        else:
            # Only reachable if a ``Prepared`` was built with a digest rather
            # than approved through :meth:`Prepared.approve`, which validates.
            # Saying so beats repeating "have not been reviewed" at someone who
            # is quite sure they reviewed them.
            reason = (
                f"were approved under {short_digest(prepared.approved_digest)}, which "
                f"is not this payload ({short_digest(prepared.digest)})"
            )
        super().__init__(
            f"{count} item(s) could not be confidently redacted and {reason}; "
            "nothing has been sent"
        )


class ApprovalMismatch(RuntimeError):
    """An approval was offered for a payload other than the one at hand.

    The wording matters more than the type. Whoever sees this did read a
    residual list and did agree to it; what they need to be told is that the
    thing in front of them is no longer that list.
    """


class RedactionFailed(RuntimeError):
    """A term the redactor was given is still in the payload. This is a bug."""


class ResponseError(RuntimeError):
    """The model's answer could not be read as findings."""


@dataclass(frozen=True, slots=True)
class SemanticRule:
    """One question worth asking a model, and what its answer costs if wrong."""

    rule_id: str
    severity: Severity
    summary: str
    instruction: str


#: The questions that survive the deterministic layer.
#:
#: Everything answerable by arithmetic was already answered: TieOut's own CO-001,
#: CO-002 and CO-003 compare labelled figures between tables, catch a
#: factor-of-ten unit error and check that a total sums. What is left is the set
#: of questions that need reading rather than counting, and this list is short on
#: purpose — a model asked to "review the deck" returns opinions, and opinions in
#: a QA report train people to skim it.
SEMANTIC_RULES: Final[dict[str, SemanticRule]] = {
    rule.rule_id: rule
    for rule in (
        SemanticRule(
            "SE-001",
            "major",
            "A statement in prose contradicts the figures it describes",
            "A headline, bullet or callout that states a figure, direction or "
            "magnitude the deck's own tables or charts do not support. Quote both "
            "sides. This is the finding that matters most, so hold it to the "
            "highest bar: if you cannot point at the number that contradicts the "
            "claim, do not report it.",
        ),
        SemanticRule(
            "SE-002",
            "minor",
            "A quantified claim no figure in the deck supports",
            "A specific quantified claim — a growth rate, a rank, a share, a "
            "multiple — with no figure anywhere in the payload to substantiate it. "
            "Not a judgement about whether the claim is true; only that the deck "
            "asserts a number it never shows.",
        ),
        SemanticRule(
            "SE-003",
            "major",
            "Period or unit drift between two statements of the same measure",
            "The same measure described against different periods or units as "
            "though they were the same: an actual compared with an estimate, a "
            "last-twelve-months figure treated as a fiscal year, a figure in "
            "millions set beside one in thousands. Name both labels.",
        ),
        SemanticRule(
            "SE-004",
            "minor",
            "A footnote marker with no matching footnote, or the reverse",
            "A superscript or bracketed marker on a slide whose footnote text is "
            "missing, or a footnote whose marker appears nowhere. Only report it "
            "where both sides are visible in the payload for that slide.",
        ),
        SemanticRule(
            "SE-005",
            "minor",
            "An enumeration that does not match its own count",
            "Text promising a count that the slide does not deliver: \"three "
            "drivers\" above four bullets, \"both options\" above three, \"the two "
            "key risks\" above one.",
        ),
        SemanticRule(
            "SE-006",
            "minor",
            "A defined measure used inconsistently",
            "A measure defined or qualified one way and then used another: "
            "\"adjusted\" in one place and unqualified in the next while quoting "
            "the same number, or a margin computed on a different base than the "
            "one its definition gives.",
        ),
    )
}

_CONFIDENCE: Final[frozenset[str]] = frozenset({"high", "medium", "low"})

#: The model is asked for bare JSON, but a fenced block is the habit of every
#: model ever trained and stripping it is cheaper than a retry.
_FENCE: Final[re.Pattern[str]] = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


@dataclass(frozen=True, slots=True)
class Prepared:
    """A payload ready to send, and whether a person has agreed to send it."""

    deck_path: str
    slide_count: int
    payload: DeckPayload
    plan: Redacted
    terms: dict[str, TermSource]
    #: The digest that was approved, not the fact that something was. ``None``
    #: means nobody has agreed to anything yet.
    approved_digest: str | None = None

    @property
    def digest(self) -> str:
        """What an approval of *this* payload has to name.

        See :mod:`tieout_review.attest`: it covers the residual list and the
        payload text together, so an approval cannot be carried from one deck
        to another simply because both redacted cleanly.
        """
        return residual_digest(self.plan.text, self.plan.residuals)

    def approve(self, digest: str) -> Prepared:
        """Record that *this* residual list has been read and accepted.

        The digest is the argument rather than the return value because that is
        what makes the approval a statement about something. ``approve()`` with
        no argument could only ever mean "yes", and "yes" to what was precisely
        the gap: over HTTP the approval arrives in a second request, by which
        time the deck, the blocklist or the forbidden-terms list may all have
        moved. Here the caller has to name the payload it is agreeing to, and a
        payload that has moved no longer answers to that name.

        Separate from :func:`send` so that the approval is still a decision
        someone made rather than a default someone forgot to change.
        """
        expected = self.digest
        if digest != expected:
            raise ApprovalMismatch(
                "the deck changed since you approved this: the approval names "
                f"{short_digest(digest)} and the payload now redacts to "
                f"{short_digest(expected)}. Nothing has been sent. Look at the "
                "redaction again — the residual list you agreed to is not the "
                "one this payload has — and approve that."
            )
        return replace(self, approved_digest=expected)

    @property
    def may_send(self) -> bool:
        return self.plan.is_clear or self.approved_digest == self.digest

    @property
    def characters(self) -> int:
        return len(self.plan.text)


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """What one review produced."""

    findings: tuple[Finding, ...]
    plan: Redacted
    model: str
    characters_sent: int
    usage: dict[str, int]
    dropped: tuple[str, ...] = ()


def prepare(
    deck: DeckModel,
    profile: Profile | None = None,
    *,
    forbidden: list[str] | None = None,
    allowlist: list[str] | None = None,
    include_notes: bool = False,
) -> Prepared:
    """Build and redact the payload. Offline, and needs no key.

    This is the whole of what ``tieout-review redact`` does, which is the point:
    the thing you inspect before trusting the tool is the same code path the
    tool uses.
    """
    terms = assemble(profile=profile, deck=deck, blocklist=forbidden or [])
    payload = extract(deck, include_notes=include_notes)
    redactor = Redactor(terms, allowlist=allowlist or [])
    plan = redactor.apply(payload.render())
    return Prepared(
        deck_path=str(deck.path),
        slide_count=deck.slide_count,
        payload=payload,
        plan=plan,
        terms=terms,
    )


def send(
    prepared: Prepared, client: ReviewClient, *, log_path: str | Path | None = None
) -> ReviewOutcome:
    """Transmit the redacted payload and turn the answer into findings.

    Three things happen before the payload is handed over, and the order is the
    safety argument rather than an implementation detail:

    1. the hold — an approval that does not name *this* payload is not an
       approval, and a residual list nobody agreed to stops the run;
    2. the re-verification — every literal term the redactor was given is
       searched for in the outgoing text, and one that survived raises
       :class:`RedactionFailed`, which is a bug report rather than a user error;
    3. the record — one line in the outbound log, written *before* the
       transport is called so that a send which dies mid-flight still leaves
       evidence. If it cannot be written the send does not happen.

    There is exactly one call to :meth:`ReviewClient.complete` in this package
    and it is the statement below. ``tests/test_review_airgap.py`` asserts both
    halves of that — that it is the only one, and that it sits after all three
    steps — because a second call site added later would be invisible to every
    behavioural test in the suite.
    """
    if not prepared.may_send:
        raise RedactionHeld(prepared)

    leaked = prepared.plan.verify(prepared.terms)
    if leaked:
        raise RedactionFailed(
            "redaction did not remove "
            + ", ".join(repr(term) for term in leaked[:5])
            + "; nothing has been sent. This is a defect in tieout_review.redact."
        )

    record_outbound(
        OutboundRecord.now(
            deck_path=prepared.deck_path,
            model=client.model,
            characters=prepared.characters,
            redactions=len(prepared.plan.redactions),
            residuals=len(prepared.plan.residuals),
            digest=prepared.approved_digest or prepared.digest,
            text_sha256=text_digest(prepared.plan.text),
        ),
        log_path,
    )

    text, usage = client.complete(
        build_system_prompt(), build_user_prompt(prepared), build_response_schema()
    )
    findings, dropped = parse_findings(text, prepared, model=client.model)
    return ReviewOutcome(
        findings=findings,
        plan=prepared.plan,
        model=client.model,
        characters_sent=prepared.characters,
        usage=usage,
        dropped=dropped,
    )


def build_response_schema() -> dict[str, object]:
    """The shape the answer must take, derived from :data:`SEMANTIC_RULES`.

    Passed to the model as a JSON schema rather than only described in the
    prompt, so the response is structurally valid by construction and
    :func:`parse_findings` is a validation step rather than a rescue operation.
    The rule enum is generated from the same table the prompt is, which is what
    stops the two drifting apart and silently dropping a whole rule's findings.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["findings"],
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["rule", "slide", "quote", "explanation", "confidence"],
                    "properties": {
                        "rule": {"type": "string", "enum": sorted(SEMANTIC_RULES)},
                        "slide": {"type": "integer", "minimum": 1},
                        "quote": {"type": "string", "minLength": 1},
                        "other": {"type": ["string", "null"]},
                        "other_slide": {"type": ["integer", "null"], "minimum": 1},
                        "explanation": {"type": "string", "minLength": 1},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                },
            }
        },
    }


def build_system_prompt() -> str:
    """The instructions, built from :data:`SEMANTIC_RULES` so the two agree."""
    rules = "\n".join(
        f"- {rule.rule_id} ({rule.severity}): {rule.summary}. {rule.instruction}"
        for rule in SEMANTIC_RULES.values()
    )
    return f"""\
You are auditing an investment banking presentation for internal inconsistency.

The deck has been redacted before reaching you. Names of companies, people,
projects and places have been replaced with stable placeholders such as
[COMPANY_1], [PERSON_2] and [CODENAME_1]. The same placeholder always means the
same entity throughout. You do not know, and must not guess, who any of them
are; speculating about identity is not part of the task and any such remark
would be discarded. Every figure is unmodified.

Each line of the payload is one piece of text, prefixed with its slide number,
the slide's archetype and the role of the text:

    [s12|table_heavy|table_row] 2025A | 1,908 | 351 | 18.4%

Report only these findings:

{rules}

Rules of engagement:

- Report only what you can point at. Every finding must quote the text it is
  about and, where the finding is a contradiction, the text it contradicts.
  A finding without a quotable other side is not a finding.
- Arithmetic between labelled table figures has already been checked
  deterministically, including cross-table contradictions, unit-scale errors and
  totals that do not sum. Do not re-report those. Your value is in the prose.
- Silence is the correct answer for a clean deck. Do not manufacture findings to
  appear thorough, and do not report matters of taste, structure, design or
  persuasiveness. None of those are in scope.
- A placeholder is opaque, not missing. Never report a placeholder as a defect.

Answer against the supplied schema. ``quote`` is the text the finding is about,
copied exactly; ``other`` is the text it contradicts, copied exactly, or null;
``other_slide`` is where that text is; ``explanation`` is one sentence naming
both figures.

An empty findings list is valid and is the expected answer for most decks.
"""


def build_user_prompt(prepared: Prepared) -> str:
    return (
        f"Deck of {prepared.slide_count} slides. Payload follows.\n\n" + prepared.plan.text
    )


def parse_findings(
    text: str, prepared: Prepared, *, model: str
) -> tuple[tuple[Finding, ...], tuple[str, ...]]:
    """Turn the model's answer into findings, discarding anything unusable.

    Dropped entries are counted and reported rather than silently ignored: a
    model that keeps returning findings this cannot read is a prompt that needs
    fixing, and hiding that would make it invisible.

    Quotes are restored to the deck's real wording here — locally, after the
    response has arrived — so the analyst reads a finding in their own language
    rather than in placeholders.
    """
    stripped = _FENCE.sub("", text.strip())
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ResponseError(f"the model did not return JSON: {stripped[:200]!r}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        raise ResponseError("the model's JSON has no 'findings' list")

    findings: list[Finding] = []
    dropped: list[str] = []
    for entry in parsed["findings"]:
        finding, reason = _finding_from(entry, prepared, model=model)
        if finding is not None:
            findings.append(finding)
        elif reason is not None:
            dropped.append(reason)
    findings.sort(key=lambda f: f.sort_key)
    return tuple(findings), tuple(dropped)


def _finding_from(
    entry: object, prepared: Prepared, *, model: str
) -> tuple[Finding | None, str | None]:
    if not isinstance(entry, dict):
        return None, "an entry that is not an object"

    rule = SEMANTIC_RULES.get(str(entry.get("rule", "")).strip().upper())
    if rule is None:
        return None, f"an unknown rule id {entry.get('rule')!r}"

    slide = _slide_index(entry.get("slide"), prepared)
    if slide is None:
        return None, f"a slide index outside the deck: {entry.get('slide')!r}"

    explanation = " ".join(str(entry.get("explanation") or "").split())
    if not explanation:
        return None, f"{rule.rule_id} with no explanation"

    quote = " ".join(str(entry.get("quote") or "").split())
    if not quote:
        return None, f"{rule.rule_id} with nothing quoted"

    other = " ".join(str(entry.get("other") or "").split())
    other_slide = _slide_index(entry.get("other_slide"), prepared)

    confidence = str(entry.get("confidence", "medium")).strip().lower()
    if confidence not in _CONFIDENCE:
        confidence = "medium"

    restore = prepared.plan.restore
    expected = restore(other) if other else None
    if expected and other_slide is not None and other_slide != slide:
        expected = f"{expected} (slide {other_slide})"

    return (
        Finding(
            rule_id=rule.rule_id,
            category=SEMANTIC_CATEGORY,
            severity=rule.severity,
            confidence=_confidence(confidence),
            where=slide,
            message=restore(explanation),
            measured=restore(quote),
            expected=expected,
            expected_provenance=(
                f"reported by {model} from a redacted payload; "
                f"{len(prepared.plan.redactions)} term(s) withheld"
            ),
        ),
        None,
    )


def _slide_index(value: object, prepared: Prepared) -> int | None:
    """A slide number the deck actually has, or None.

    A model that invents a slide number produces a finding pointing at nothing,
    and a report the analyst cannot check is worse than no report.
    """
    try:
        index = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return index if 1 <= index <= prepared.slide_count else None


def _confidence(value: str) -> Confidence:
    if value == "high":
        return "high"
    if value == "low":
        return "low"
    return "medium"
