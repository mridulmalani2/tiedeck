"""The interview.

The product claim is that onboarding costs almost nothing, so the interview's job
is mostly to *not* ask. A question the engine could have answered from evidence
is a broken promise, and a low-value question that crowds out a high-value one
under the twelve-question cap is the same failure in a subtler form.
"""

from __future__ import annotations

import pytest

from tieout.learn.classify import QuestionDraft
from tieout.learn.interview import (
    MAX_QUESTIONS,
    apply_hygiene_answers,
    hygiene_questions,
    rank,
    run_interview,
    summarise,
    to_deferred,
)


class _AcceptDefaults:
    """A user pressing enter at every prompt."""

    def ask(self, question):
        return ""


class _Chooser:
    """A user answering deliberately."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def ask(self, question):
        self.asked.append(question.id)
        return self.answer


def _draft(identifier: str, *, impact: int = 1, kind: str = "generic", default: str = "d"):
    return QuestionDraft(
        id=identifier,
        field_path=f"path.{identifier}",
        question=f"{identifier}?",
        options=["first", "second"],
        default=default,
        impact=impact,
        kind=kind,
    )


# --------------------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------------------


def test_questions_are_ordered_by_priority_band_then_impact():
    drafts = [
        _draft("generic", kind="generic", impact=99),
        _draft("outlier", kind="dominant_outlier", impact=1),
        _draft("terms", kind="terminology", impact=1),
    ]
    assert [d.id for d in rank(drafts)] == ["outlier", "terms", "generic"]


def test_impact_breaks_ties_within_a_band():
    drafts = [
        _draft("low", kind="terminology", impact=2),
        _draft("high", kind="terminology", impact=40),
    ]
    assert [d.id for d in rank(drafts)] == ["high", "low"]


def test_ranking_is_deterministic_for_identical_drafts():
    drafts = [_draft("b", impact=5), _draft("a", impact=5)]
    assert [d.id for d in rank(drafts)] == ["a", "b"]


def test_the_twelve_question_cap_holds_and_drops_the_least_valuable():
    drafts = [_draft(f"q{index:02d}", impact=index) for index in range(20)]
    result = run_interview(drafts)
    assert len(result.questions) == MAX_QUESTIONS
    assert len(result.dropped) == 20 - MAX_QUESTIONS
    kept = {q.id for q in result.questions}
    assert "q19" in kept, "the highest-impact question must survive the cap"
    assert "q00" not in kept, "the lowest-impact question must be the one dropped"


# --------------------------------------------------------------------------------------
# Non-interactive mode
# --------------------------------------------------------------------------------------


def test_non_interactive_mode_applies_every_default_and_asks_nothing():
    result = run_interview([_draft("q1", default="safe")])
    assert result.asked == []
    assert len(result.deferred) == 1
    assert result.answers["q1"] == "safe"
    assert not result.locks, "nothing is locked by a default the user never saw"


def test_a_deferred_question_survives_into_the_profile():
    result = run_interview([_draft("q1")])
    question = result.deferred[0]
    assert question.answer == "d"
    assert not question.answered
    assert question.options == ["first", "second"]


def test_the_summary_tells_the_user_how_to_answer_later():
    summary = summarise(run_interview([_draft("q1")]))
    assert "deferred 1" in summary
    assert "learn --review" in summary


def test_no_questions_is_reported_as_a_positive_outcome():
    assert summarise(run_interview([])).startswith("No questions")


def test_the_summary_mentions_dropped_questions():
    drafts = [_draft(f"q{index:02d}", impact=index) for index in range(15)]
    summary = summarise(run_interview(drafts))
    assert "dropped 3" in summary


# --------------------------------------------------------------------------------------
# Interactive mode
# --------------------------------------------------------------------------------------


def test_pressing_enter_accepts_the_default_but_still_counts_as_answered():
    result = run_interview([_draft("q1", default="safe")], prompter=_AcceptDefaults())
    assert len(result.asked) == 1
    assert result.answers["q1"] == "safe"


def test_an_explicit_answer_locks_its_field():
    """Section 8.6: answering explicitly means a later --add cannot quietly
    overwrite a decision a person made."""
    result = run_interview([_draft("q1")], prompter=_Chooser("second"))
    assert result.answers["q1"] == "second"
    assert result.locks == ["path.q1"]


def test_a_field_is_locked_only_once():
    drafts = [_draft("a"), _draft("b")]
    drafts[1].field_path = drafts[0].field_path
    result = run_interview(drafts, prompter=_Chooser("x"))
    assert result.locks == [drafts[0].field_path]


def test_questions_are_asked_in_ranked_order():
    chooser = _Chooser("x")
    run_interview(
        [_draft("generic", kind="generic"), _draft("outlier", kind="dominant_outlier")],
        prompter=chooser,
    )
    assert chooser.asked == ["outlier", "generic"]


def test_to_deferred_carries_everything_the_profile_needs():
    question = to_deferred(_draft("q1", impact=7))
    assert (question.id, question.impact, question.default) == ("q1", 7, "d")
    assert question.field_path == "path.q1"


# --------------------------------------------------------------------------------------
# Hygiene questions are conditional on evidence
# --------------------------------------------------------------------------------------


def test_a_clean_deck_raises_no_hygiene_questions(clean_deck):
    """Section 8.4 lists four settings that cannot be inferred. Asked
    unconditionally they would fire on every deck, and section 14's requirement
    that a clean reference deck produce zero questions would be unachievable.

    A deck with no notes, no hidden slides and no metadata has nothing ambiguous
    about it, so the defaults stand silently.
    """
    assert hygiene_questions(clean_deck) == []


def test_speaker_notes_in_the_reference_deck_raise_a_question(dirty_deck):
    drafts = hygiene_questions(dirty_deck)
    notes = [d for d in drafts if d.id == "hygiene-notes"]
    assert notes, [d.id for d in drafts]
    assert notes[0].default == "no, flag them"
    assert notes[0].slides


def test_hidden_slides_raise_a_question(dirty_deck):
    assert any(d.id == "hygiene-hidden" for d in hygiene_questions(dirty_deck))


def test_populated_metadata_raises_a_question(variant_decks):
    drafts = hygiene_questions(variant_decks["metadata"])
    metadata = [d for d in drafts if d.id == "hygiene-metadata"]
    assert metadata
    assert "creator" in metadata[0].question


def test_every_hygiene_question_has_a_safe_default(dirty_deck):
    for draft in hygiene_questions(dirty_deck):
        assert draft.default, f"{draft.id} has no default to accept"
        assert draft.default in draft.options


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("yes, allow them", True), ("no, flag them", False), ("YES", True)],
)
def test_hygiene_answers_become_profile_booleans(answer, expected):
    assert apply_hygiene_answers({"hygiene-notes": answer}) == {
        "hygiene.allow_speaker_notes": expected
    }


def test_the_metadata_answer_is_inverted_because_the_question_is():
    """The question asks "should TieOut flag this?", so "yes" means do not allow."""
    assert apply_hygiene_answers({"hygiene-metadata": "yes, flag it"}) == {
        "hygiene.allow_document_metadata": False
    }
    assert apply_hygiene_answers({"hygiene-metadata": "no, allow it"}) == {
        "hygiene.allow_document_metadata": True
    }


def test_unanswered_hygiene_questions_change_nothing():
    assert apply_hygiene_answers({}) == {}


# --------------------------------------------------------------------------- #
# Answers have to reach the field they are about
# --------------------------------------------------------------------------- #


def test_an_answered_hygiene_question_changes_the_field():
    """Recording the answer without applying it is worse than not asking.

    A profile could say ``allow_speaker_notes: false`` directly under a `why:`
    note claiming the user answered "yes, allow them", which contradicts itself
    in writing.
    """
    from tieout.learn import apply_answers
    from tieout.profile.schema import DeferredQuestion, Profile, SlideProfile

    profile = Profile(
        client="acme",
        slide=SlideProfile(width_pt=960.0, height_pt=540.0),
        questions=[
            DeferredQuestion(
                id="hygiene-notes",
                field_path="hygiene.allow_speaker_notes",
                question="are notes acceptable?",
                options=["no, flag them", "yes, allow them"],
                default="no, flag them",
                answered=True,
                answer="yes, allow them",
            )
        ],
    )
    before = profile.hygiene.allow_speaker_notes
    assert before is False

    applied, recorded_only = apply_answers(profile)

    after = profile.hygiene.allow_speaker_notes
    assert after is True
    assert applied == ["hygiene.allow_speaker_notes"]
    assert recorded_only == []


def test_a_conservative_answer_leaves_the_field_alone():
    from tieout.learn import apply_answers
    from tieout.profile.schema import DeferredQuestion, Profile, SlideProfile

    profile = Profile(
        client="acme",
        slide=SlideProfile(width_pt=960.0, height_pt=540.0),
        questions=[
            DeferredQuestion(
                id="hygiene-hidden",
                field_path="hygiene.allow_hidden_slides",
                question="are hidden slides acceptable?",
                answered=True,
                answer="no, flag them",
            )
        ],
    )
    apply_answers(profile)
    assert profile.hygiene.allow_hidden_slides is False


def test_an_answer_that_cannot_be_applied_is_reported_rather_than_claimed():
    """A palette or terminology answer is folded in by the deriver against
    evidence a review run does not have. Saying so is the honest outcome."""
    from tieout.learn import apply_answers
    from tieout.profile.schema import DeferredQuestion, Profile, SlideProfile

    profile = Profile(
        client="acme",
        slide=SlideProfile(width_pt=960.0, height_pt=540.0),
        questions=[
            DeferredQuestion(
                id="palette-outlier-1",
                field_path="brand.palette_hex",
                question="include the fifth colour?",
                answered=True,
                answer="yes, include it",
            )
        ],
    )
    applied, recorded_only = apply_answers(profile)
    assert applied == []
    assert recorded_only == ["brand.palette_hex"]


def test_an_unanswered_question_changes_nothing():
    from tieout.learn import apply_answers
    from tieout.profile.schema import DeferredQuestion, Profile, SlideProfile

    profile = Profile(
        client="acme",
        slide=SlideProfile(width_pt=960.0, height_pt=540.0),
        questions=[
            DeferredQuestion(
                id="hygiene-notes",
                field_path="hygiene.allow_speaker_notes",
                question="are notes acceptable?",
                default="no, flag them",
            )
        ],
    )
    assert apply_answers(profile) == ([], [])
    assert profile.hygiene.allow_speaker_notes is False
