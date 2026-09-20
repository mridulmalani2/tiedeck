"""Fail-closed transmission, and reading the answer back.

The tests that matter most in this file are the ones that assert nothing was
sent. A stub client records whether it was called at all, so "refused" is a
property of the transport being untouched rather than of an exception type.
"""

from __future__ import annotations

import json

import pytest

from tieout.rules.base import NON_GATING_CATEGORIES, AuditResult, Finding
from tieout_review.redact import Redactor, TermSource
from tieout_review.review import (
    SEMANTIC_CATEGORY,
    SEMANTIC_RULES,
    RedactionFailed,
    RedactionHeld,
    ResponseError,
    build_response_schema,
    build_system_prompt,
    build_user_prompt,
    parse_findings,
    prepare,
    send,
)


class StubClient:
    """A transport that records what it was asked to send, and whether it was."""

    def __init__(self, answer: object = None, model: str = "stub-model") -> None:
        self._answer = answer if answer is not None else {"findings": []}
        self.model = model
        self.calls: list[tuple[str, str]] = []
        self.schema: dict[str, object] = {}

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> tuple[str, dict[str, int]]:
        self.calls.append((system, user))
        self.schema = schema
        text = self._answer if isinstance(self._answer, str) else json.dumps(self._answer)
        return text, {"input_tokens": 100, "output_tokens": 10}


@pytest.fixture
def prepared_clean(clean_deck, reference_profile):
    return prepare(clean_deck, reference_profile, forbidden=["Ashcombe Partners"])


@pytest.fixture
def prepared_held(prepared_clean):
    """A payload with something the redactor could not clear.

    Constructed rather than found: the reference deck happens to redact cleanly,
    and the refusal path is the most important behaviour in this module, so it
    must not be a test that skips itself when a fixture changes.
    """
    import dataclasses

    plan = Redactor().apply("We rate Halvorsen Estates the strongest of the three")
    assert plan.residuals, "the constructed payload must actually have a residual"
    return dataclasses.replace(prepared_clean, plan=plan)


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #


def test_preparation_needs_no_key_and_no_network(clean_deck, reference_profile):
    """The command an analyst runs to decide whether to trust this at all."""
    prepared = prepare(clean_deck, reference_profile)
    assert prepared.characters > 0
    assert prepared.approved_digest is None


def test_a_payload_with_residuals_outstanding_is_not_sent(prepared_held):
    client = StubClient()
    with pytest.raises(RedactionHeld) as excinfo:
        send(prepared_held, client)
    assert client.calls == [], "the transport must not have been touched"
    assert "nothing has been sent" in str(excinfo.value)
    assert excinfo.value.prepared is prepared_held


def test_the_same_payload_is_sent_once_a_person_has_approved_it(prepared_held):
    """Approval is the only thing that changes, and it is enough."""
    client = StubClient()
    send(prepared_held.approve(prepared_held.digest), client)
    assert len(client.calls) == 1


def test_approval_is_an_act_and_not_a_default(prepared_clean):
    assert prepared_clean.approved_digest is None
    approved = prepared_clean.approve(prepared_clean.digest)
    assert approved.approved_digest == prepared_clean.digest
    assert prepared_clean.approved_digest is None, "approve() must not mutate in place"


def test_a_clear_payload_needs_no_approval(prepared_clean):
    assert prepared_clean.plan.is_clear, "the reference deck is expected to redact cleanly"
    assert prepared_clean.may_send


def test_a_held_payload_does_not_claim_it_may_send(prepared_held):
    assert not prepared_held.may_send


def test_a_term_surviving_redaction_stops_the_send(clean_deck, reference_profile):
    """The last line of defence: a rule that silently did not fire.

    Simulated by adding a term to the prepared payload's term list after the
    redaction has run, which is the same observable state as a pattern that
    failed to match.
    """
    prepared = prepare(clean_deck, reference_profile)
    prepared.terms["Revenue"] = TermSource("custom", "blocklist")
    client = StubClient()
    with pytest.raises(RedactionFailed) as excinfo:
        send(prepared.approve(prepared.digest), client)
    assert "Revenue" in str(excinfo.value)
    assert client.calls == []


def test_nothing_identifying_reaches_the_transport(clean_deck, reference_profile):
    """The end-to-end property, asserted against what the stub actually received."""
    prepared = prepare(clean_deck, reference_profile, forbidden=["Ashcombe Partners"])
    client = StubClient()
    send(prepared.approve(prepared.digest), client)
    (_, user) = client.calls[0]
    for term in prepared.terms:
        assert term.lower() not in user.lower(), term


def test_the_figures_do_reach_the_transport(clean_deck, reference_profile):
    """The other half of the bargain, and the reason for the whole exercise."""
    prepared = prepare(clean_deck, reference_profile, forbidden=["Ashcombe Partners"])
    client = StubClient()
    send(prepared.approve(prepared.digest), client)
    (_, user) = client.calls[0]
    assert "1,908" in user
    assert "18.4%" in user


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #


def test_the_prompt_lists_every_semantic_rule():
    """Drift between the prompt and the parser would silently drop findings."""
    prompt = build_system_prompt()
    for rule_id, rule in SEMANTIC_RULES.items():
        assert rule_id in prompt
        assert rule.summary in prompt


def test_the_prompt_tells_the_model_not_to_redo_the_arithmetic():
    prompt = build_system_prompt()
    assert "already been checked" in prompt
    assert "Silence is the correct answer" in prompt


def test_the_prompt_explains_the_placeholders():
    assert "[COMPANY_1]" in build_system_prompt()


def test_the_response_schema_enumerates_exactly_the_known_rules():
    """Derived from the same table as the prompt, or a whole rule's findings
    would be dropped in silence once the two drifted apart."""
    schema = json.loads(json.dumps(build_response_schema()))
    rule = schema["properties"]["findings"]["items"]["properties"]["rule"]
    assert rule["enum"] == sorted(SEMANTIC_RULES)


def test_the_schema_travels_with_the_request(clean_deck, reference_profile):
    prepared = prepare(clean_deck, reference_profile, forbidden=["Ashcombe Partners"])
    client = StubClient()
    send(prepared.approve(prepared.digest), client)
    assert client.schema == build_response_schema()


def test_the_user_message_is_the_redacted_payload_and_nothing_else(prepared_clean):
    assert prepared_clean.plan.text in build_user_prompt(prepared_clean)


# --------------------------------------------------------------------------- #
# Reading the answer
# --------------------------------------------------------------------------- #


def _parse(answer, prepared) -> tuple[tuple[Finding, ...], tuple[str, ...]]:
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return parse_findings(text, prepared, model="stub-model")


def test_a_well_formed_finding_becomes_a_finding(prepared_clean):
    findings, dropped = _parse(
        {
            "findings": [
                {
                    "rule": "SE-001",
                    "slide": 4,
                    "quote": "Revenue grew 20%",
                    "other": "1,908 from 1,562",
                    "other_slide": 6,
                    "explanation": "The table shows 22.2%, not 20%.",
                    "confidence": "high",
                }
            ]
        },
        prepared_clean,
    )
    assert dropped == ()
    (finding,) = findings
    assert finding.rule_id == "SE-001"
    assert finding.category == SEMANTIC_CATEGORY
    assert finding.severity == "major"
    assert finding.confidence == "high"
    assert finding.slide_index == 4
    assert finding.measured == "Revenue grew 20%"
    assert finding.expected is not None
    assert "slide 6" in finding.expected
    assert finding.measured is not None


def test_an_empty_answer_is_valid_and_is_the_expected_one(prepared_clean):
    assert _parse({"findings": []}, prepared_clean) == ((), ())


def test_a_fenced_json_block_is_accepted(prepared_clean):
    """Every model reaches for a code fence; stripping it is cheaper than a retry."""
    findings, _ = _parse('```json\n{"findings": []}\n```', prepared_clean)
    assert findings == ()


def test_a_quote_is_restored_to_the_deck_s_own_wording(clean_deck, reference_profile):
    """The model answers in placeholders; the analyst reads their own language."""
    prepared = prepare(clean_deck, reference_profile, forbidden=["Ashcombe Partners"])
    findings, _ = _parse(
        {
            "findings": [
                {
                    "rule": "SE-002",
                    "slide": 2,
                    "quote": "[TERM_1] analysis",
                    "explanation": "[TERM_1] asserts a figure it never shows.",
                }
            ]
        },
        prepared,
    )
    (finding,) = findings
    assert finding.measured is not None
    assert "[TERM_" not in finding.measured
    assert "[TERM_" not in finding.message


def test_a_finding_records_that_it_came_from_a_model_and_from_a_redacted_payload(
    prepared_clean,
):
    """Provenance is how a reader decides how much weight to give a finding."""
    findings, _ = _parse(
        {
            "findings": [
                {
                    "rule": "SE-005",
                    "slide": 3,
                    "quote": "three drivers",
                    "explanation": "Four bullets follow.",
                }
            ]
        },
        prepared_clean,
    )
    provenance = findings[0].expected_provenance
    assert provenance is not None
    assert "stub-model" in provenance
    assert "redacted" in provenance


@pytest.mark.parametrize(
    ("entry", "because"),
    [
        ({"rule": "SE-999", "slide": 1, "quote": "x", "explanation": "y"}, "unknown rule"),
        ({"rule": "SE-001", "slide": 999, "quote": "x", "explanation": "y"}, "slide index"),
        ({"rule": "SE-001", "slide": 0, "quote": "x", "explanation": "y"}, "slide index"),
        ({"rule": "SE-001", "slide": "later", "quote": "x", "explanation": "y"}, "slide"),
        ({"rule": "SE-001", "slide": 1, "quote": "", "explanation": "y"}, "nothing quoted"),
        ({"rule": "SE-001", "slide": 1, "quote": "x", "explanation": ""}, "no explanation"),
        ("not an object", "not an object"),
    ],
)
def test_an_unusable_answer_is_dropped_and_counted(prepared_clean, entry, because):
    """Counted rather than silently ignored: a model that keeps returning
    findings this cannot read is a prompt that needs fixing."""
    findings, dropped = _parse({"findings": [entry]}, prepared_clean)
    assert findings == ()
    assert len(dropped) == 1
    assert because in dropped[0]


def test_an_invented_slide_number_is_refused(prepared_clean):
    """A finding pointing at nothing is worse than no finding."""
    _, dropped = _parse(
        {
            "findings": [
                {
                    "rule": "SE-001",
                    "slide": prepared_clean.slide_count + 5,
                    "quote": "x",
                    "explanation": "y",
                }
            ]
        },
        prepared_clean,
    )
    assert dropped


def test_an_unknown_confidence_falls_back_to_medium(prepared_clean):
    findings, _ = _parse(
        {
            "findings": [
                {
                    "rule": "SE-001",
                    "slide": 1,
                    "quote": "x",
                    "explanation": "y",
                    "confidence": "certain",
                }
            ]
        },
        prepared_clean,
    )
    assert findings[0].confidence == "medium"


@pytest.mark.parametrize("answer", ["not json at all", "{}", '{"findings": "none"}', "[]"])
def test_an_unreadable_answer_raises(prepared_clean, answer):
    with pytest.raises(ResponseError):
        _parse(answer, prepared_clean)


def test_findings_come_back_in_report_order(prepared_clean):
    findings, _ = _parse(
        {
            "findings": [
                {"rule": "SE-002", "slide": 9, "quote": "b", "explanation": "b"},
                {"rule": "SE-001", "slide": 2, "quote": "a", "explanation": "a"},
            ]
        },
        prepared_clean,
    )
    assert [finding.slide_index for finding in findings] == [2, 9]


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #


def test_the_semantic_category_is_declared_non_gating():
    assert SEMANTIC_CATEGORY in NON_GATING_CATEGORIES


def test_a_semantic_finding_does_not_change_an_existing_exit_code():
    """Adding this layer to a pipeline must not fail a build that used to pass.

    A probabilistic finding gating a deterministic gate would make the exit code
    mean something different from one run to the next.
    """
    result = AuditResult(deck_path="x", client="c", profile_version=1, generated_at="now")
    result.findings.append(
        Finding(
            rule_id="SE-001",
            category=SEMANTIC_CATEGORY,
            severity="major",
            confidence="high",
            where=4,
            message="a contradiction",
        )
    )
    assert not result.exceeds("major")
    assert result.exceeds("major", include_non_gating=True)


def test_a_deterministic_finding_still_gates():
    result = AuditResult(deck_path="x", client="c", profile_version=1, generated_at="now")
    result.findings.append(
        Finding(
            rule_id="CO-001",
            category="consistency",
            severity="major",
            confidence="high",
            where=4,
            message="a contradiction",
        )
    )
    assert result.exceeds("major")


def test_every_semantic_rule_is_reportable_and_none_is_a_blocker():
    """A model's finding should never be the thing that blocks a send.

    It can be the thing that makes someone look, which is what major is for.
    """
    for rule in SEMANTIC_RULES.values():
        assert rule.severity in ("major", "minor")
        assert rule.rule_id.startswith("SE-")
        assert rule.instruction.strip()


def test_the_outcome_reports_what_it_sent(clean_deck, reference_profile):
    prepared = prepare(clean_deck, reference_profile, forbidden=["Ashcombe Partners"])
    outcome = send(prepared.approve(prepared.digest), StubClient())
    assert outcome.characters_sent == prepared.characters
    assert outcome.model == "stub-model"
    assert outcome.usage["input_tokens"] == 100


def test_the_redaction_plan_travels_with_the_outcome(clean_deck, reference_profile):
    """A report has to be able to say what was withheld."""
    prepared = prepare(clean_deck, reference_profile, forbidden=["Ashcombe Partners"])
    outcome = send(prepared.approve(prepared.digest), StubClient())
    assert outcome.plan.redactions == prepared.plan.redactions


def test_an_allowlist_entry_clears_a_residual():
    """Clearing a residual has to be persistent, or the review is done every run."""
    corpus = "We rate Halvorsen Estates the strongest of the three"
    before = Redactor().apply(corpus)
    after = Redactor(allowlist=[before.residuals[0].text]).apply(corpus)
    assert len(after.residuals) < len(before.residuals)


def test_the_redactor_and_the_review_layer_agree_on_placeholder_shape():
    """The prompt documents a shape; the redactor has to emit it."""
    out = Redactor({"Vantara": TermSource("company", "blocklist")}).apply("Vantara")
    assert out.text == "[COMPANY_1]"
    assert "[COMPANY_1]" in build_system_prompt()
