"""Question ranking, prompting and defaults.

The product claim is that onboarding costs almost nothing, so the interview's job
is mostly to *not* ask. Every question is a tax on that claim, and a question the
engine could have answered from evidence is a broken promise.

Three rules follow from that:

* **Only ask what cannot be inferred.** The derivers draft questions; nothing is
  asked unless the evidence genuinely failed to settle it.
* **Hard cap of twelve**, ranked by expected impact, measured as the number of
  future findings that would depend on the answer. A question about the logo's
  position affects every slide; one about a footnote affects one.
* **Every question has a safe default**, so the whole interview can be accepted
  by pressing enter, and non-interactive mode is the same thing with the prompts
  suppressed.

One interpretation worth stating. Section 8.4 lists four hygiene settings that
"cannot be inferred at all" and are to be asked as yes/no with a default. Asked
unconditionally, those four would fire on every deck, and the acceptance
criterion that the clean reference deck produces zero questions would be
impossible to meet. They are therefore asked only when the deck shows contrary
evidence -- notes present, hidden slides present, metadata populated. A deck with
none of those has nothing ambiguous about it, and the defaults stand silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Protocol

from tieout.learn.classify import QuestionDraft
from tieout.model.deck import DeckModel
from tieout.profile.schema import DeferredQuestion

#: Hard cap from section 8.4.
MAX_QUESTIONS: Final[int] = 12

#: Priority bands, applied before impact. Section 8.4's ordering.
_KIND_PRIORITY: Final[dict[str, int]] = {
    "dominant_outlier": 0,
    "terminology": 1,
    "logo_variant": 2,
    "confidentiality_scope": 3,
    "hygiene": 4,
    "generic": 5,
}


class Prompter(Protocol):
    """How the interview asks. Implemented by the CLI and by tests."""

    def ask(self, question: DeferredQuestion) -> str:
        """Return the chosen answer, or the default when the user just confirms."""


@dataclass
class InterviewResult:
    """What the interview settled and what it deferred."""

    #: Every question considered, in the order asked, answered or not.
    questions: list[DeferredQuestion] = field(default_factory=list)
    #: Question id -> answer, for the derivers to fold in.
    answers: dict[str, str] = field(default_factory=dict)
    #: Field paths the user answered explicitly, which section 8.6 says enter
    #: ``locks`` automatically so re-learning cannot overwrite a human decision.
    locks: list[str] = field(default_factory=list)
    #: Drafts dropped because they exceeded the twelve-question cap.
    dropped: list[QuestionDraft] = field(default_factory=list)

    @property
    def asked(self) -> list[DeferredQuestion]:
        return [q for q in self.questions if q.answered]

    @property
    def deferred(self) -> list[DeferredQuestion]:
        return [q for q in self.questions if not q.answered]


def hygiene_questions(deck: DeckModel) -> list[QuestionDraft]:
    """Hygiene questions, drafted only where the deck gives contrary evidence.

    A reference deck with no speaker notes has already answered "are notes
    allowed?" as far as the evidence goes, and asking anyway spends a question
    that a real ambiguity elsewhere needs.
    """
    drafts: list[QuestionDraft] = []

    with_notes = [s.index for s in deck.slides if s.notes_text.strip()]
    if with_notes:
        drafts.append(
            QuestionDraft(
                id="hygiene-notes",
                field_path="hygiene.allow_speaker_notes",
                question=(
                    f"{len(with_notes)} slide"
                    f"{'s carry' if len(with_notes) != 1 else ' carries'} speaker "
                    f"notes (slide{'s' if len(with_notes) != 1 else ''} "
                    f"{', '.join(str(i) for i in with_notes[:5])}). Are notes "
                    f"acceptable in decks sent to this client?"
                ),
                options=["no, flag them", "yes, allow them"],
                default="no, flag them",
                impact=len(with_notes),
                kind="hygiene",
                slides=tuple(with_notes),
            )
        )

    if deck.hidden_slide_indices:
        hidden = deck.hidden_slide_indices
        drafts.append(
            QuestionDraft(
                id="hygiene-hidden",
                field_path="hygiene.allow_hidden_slides",
                question=(
                    f"The reference deck contains {len(hidden)} hidden slide"
                    f"{'s' if len(hidden) != 1 else ''} "
                    f"({', '.join(str(i) for i in hidden[:5])}). Are hidden slides "
                    f"acceptable, or should they be flagged?"
                ),
                options=["no, flag them", "yes, allow them"],
                default="no, flag them",
                impact=len(hidden),
                kind="hygiene",
                slides=hidden,
            )
        )

    leaking = {
        **deck.package.core.identifying_fields(),
        **deck.package.app.identifying_fields(),
    }
    if leaking:
        drafts.append(
            QuestionDraft(
                id="hygiene-metadata",
                field_path="hygiene.allow_document_metadata",
                question=(
                    "The reference deck's document properties name "
                    + ", ".join(f"{k}={v!r}" for k, v in sorted(leaking.items()))
                    + ". These are recorded as the client's own and will not be "
                    "flagged. Should TieOut still check for other names in future "
                    "decks?"
                ),
                options=["yes, flag other names", "no, ignore metadata entirely"],
                default="yes, flag other names",
                impact=len(leaking) * 3,
                kind="hygiene",
            )
        )

    return drafts


def rank(drafts: list[QuestionDraft]) -> list[QuestionDraft]:
    """Order drafts by priority band, then by descending impact.

    Impact is the number of future findings that would depend on the answer, so
    the ordering is "what would go wrong if we guessed" rather than "what is
    interesting". Ties break on the question id, so two runs of the same command
    ask in the same order -- determinism matters as much here as anywhere.
    """
    return sorted(
        drafts,
        key=lambda d: (_KIND_PRIORITY.get(d.kind, 9), -d.impact, d.id),
    )


def to_deferred(draft: QuestionDraft) -> DeferredQuestion:
    return DeferredQuestion(
        id=draft.id,
        field_path=draft.field_path,
        question=draft.question,
        options=list(draft.options),
        default=draft.default,
        impact=draft.impact,
    )


def run_interview(
    drafts: list[QuestionDraft],
    *,
    prompter: Prompter | None = None,
) -> InterviewResult:
    """Rank, cap and either ask or defer.

    With no prompter this is non-interactive mode: every default is applied, each
    question is carried into the profile as a deferred question so it appears as
    a ``QUESTION:`` comment, and any rule that depended on a defaulted answer is
    marked medium confidence by the emitter.
    """
    result = InterviewResult()
    ordered = rank(drafts)
    result.dropped = ordered[MAX_QUESTIONS:]

    for draft in ordered[:MAX_QUESTIONS]:
        question = to_deferred(draft)
        if prompter is None:
            question.answer = draft.default
            question.answered = False
            result.questions.append(question)
            result.answers[draft.id] = draft.default
            continue

        answer = prompter.ask(question).strip() or draft.default
        question.answer = answer
        question.answered = True
        result.questions.append(question)
        result.answers[draft.id] = answer
        # Section 8.6: an explicitly answered question locks its field, so a later
        # `learn --add` cannot quietly overwrite a decision a person made.
        if draft.field_path not in result.locks:
            result.locks.append(draft.field_path)

    return result


def summarise(result: InterviewResult) -> str:
    """The line printed after a non-interactive run.

    Says how many questions were deferred and how to answer them, because a
    profile with unanswered questions in it is only useful if the user knows they
    are there.
    """
    answered = len(result.asked)
    deferred = len(result.deferred)
    dropped = len(result.dropped)

    if not result.questions and not dropped:
        return "No questions: every rule was settled from the evidence."

    parts: list[str] = []
    if answered:
        parts.append(f"answered {answered}")
    if deferred:
        parts.append(
            f"deferred {deferred} (applied the default, see QUESTION comments in "
            f"the profile, and run `tieout learn --review` to answer them)"
        )
    if dropped:
        parts.append(
            f"dropped {dropped} below the {MAX_QUESTIONS}-question cap as too "
            f"low impact to be worth asking"
        )
    total = answered + deferred + dropped
    return f"{total} question{'s' if total != 1 else ''}: " + "; ".join(parts) + "."


def apply_hygiene_answers(answers: dict[str, str]) -> dict[str, bool]:
    """Translate hygiene answers into profile booleans.

    Answers are matched on their leading word so a user typing "yes" or picking
    the option verbatim both work.
    """
    out: dict[str, bool] = {}
    mapping = {
        "hygiene-notes": ("hygiene.allow_speaker_notes", "yes"),
        "hygiene-hidden": ("hygiene.allow_hidden_slides", "yes"),
        "hygiene-metadata": ("hygiene.allow_document_metadata", "no"),
    }
    for question_id, (field_path, permissive_prefix) in mapping.items():
        answer = answers.get(question_id)
        if answer is None:
            continue
        out[field_path] = answer.strip().casefold().startswith(permissive_prefix)
    return out
