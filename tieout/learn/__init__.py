"""The learning pipeline.

``tieout learn REFERENCE.pptx --client NAME`` lands here. The order matters:

1. Load and classify the deck, because every deriver is scoped by archetype.
2. Identify furniture, because the logo and footer must be excluded from margin,
   grid and font-role derivation and included in the palette.
3. Run the four derivers, each of which records its own provenance and its own
   ``not_learned`` entries.
4. Run the interview over the questions the derivers drafted, plus the hygiene
   questions the deck itself raises.
5. Assemble the profile and fold the answers in.

Steps 3 and 5 are separate on purpose. A deriver that both decided a value and
asked about it would make the question's effect invisible; keeping assembly last
means every answer is applied in one place where it can be seen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tieout.learn.classify import Derivation, QuestionDraft
from tieout.learn.derive_brand import derive_brand
from tieout.learn.derive_layout import derive_gutter, derive_layout
from tieout.learn.derive_terms import apply_canon_answers, derive_terms
from tieout.learn.derive_typography import derive_typography
from tieout.learn.interview import (
    InterviewResult,
    Prompter,
    apply_hygiene_answers,
    hygiene_questions,
    run_interview,
    summarise,
)
from tieout.model.deck import DeckModel
from tieout.model.furniture import detect_furniture
from tieout.model.loader import load_deck

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, annotation only
    from tieout.rules.base import Finding
from tieout.profile.schema import (
    HygieneProfile,
    NotLearned,
    Profile,
    RulesProfile,
    SlideProfile,
)

__all__ = ["LearnResult", "learn", "learn_from_decks"]

#: Rules disabled in a freshly learned profile.
#:
#: TY-009 needs a client dictionary before it is worth reading, and LO-006 is
#: approximate and needs the real font files, so both ship off and are opted into
#: through ``rules.enabled``. Section 8.5's own worked example disables TY-009 for
#: the same reason.
DEFAULT_DISABLED_RULES: tuple[str, ...] = ("TY-009", "LO-006")


@dataclass
class ReferenceReview:
    """What the learned profile still reports about the deck it was learned from.

    A profile derived from a deck and then run against that same deck should
    find nothing: that is the tool's own acceptance criterion, and every
    deviation is one of exactly two things.

    Either the profile is wrong -- it derived a central value where the deck
    holds a range, and now reports the deck's own spread -- which is a defect in
    TieOut and belongs in its test suite. Or the reference deck really does
    contain what the rule says: a typeface nobody meant to use, a colour used
    once, a double space. That is worth knowing *before* the profile goes into
    service, because every one of those defects is about to become the standard
    every future deck is measured against, or a finding on every future deck
    that inherits it.

    So this is reported, never silently absorbed. Widening the profile to cover
    a defect would make the invariant hold by making the tool useless, and
    recording blanket exemptions would do the same more quietly.
    """

    findings: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def by_rule(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = {}
        for finding in sorted(self.findings, key=lambda f: f.sort_key):
            grouped.setdefault(finding.rule_id, []).append(finding)
        return grouped

    @property
    def blocking_count(self) -> int:
        return sum(1 for f in self.findings if f.severity in ("blocker", "major"))

    def describe(self) -> str:
        """One line per rule, with the slides and the first example."""
        if self.clean:
            return (
                "The reference deck passes its own profile: every rule in the "
                "catalogue is silent on the deck it was learned from."
            )
        lines = [
            f"{len(self.findings)} finding(s) remain on the reference deck itself. "
            "The profile does not cover these, so they are either defects in the "
            "deck worth fixing before it becomes the house standard, or "
            "conventions to accept:"
        ]
        for rule_id, findings in self.by_rule.items():
            slides = sorted({f.slide_index for f in findings if f.slide_index})
            where = ", ".join(str(index) for index in slides[:8])
            if len(slides) > 8:
                where += f", +{len(slides) - 8} more"
            lines.append(
                f"  [{findings[0].severity}] {rule_id} on slide(s) {where}: "
                f"{findings[0].message}"
            )
        return "\n".join(lines)


@dataclass
class LearnResult:
    """A learned profile and an account of how it was produced."""

    profile: Profile
    interview: InterviewResult = field(default_factory=InterviewResult)
    derivation: Derivation = field(default_factory=Derivation)
    decks: list[DeckModel] = field(default_factory=list)
    reference_review: ReferenceReview = field(default_factory=ReferenceReview)

    @property
    def summary(self) -> str:
        return summarise(self.interview)

    @property
    def question_count(self) -> int:
        return len(self.interview.questions) + len(self.interview.dropped)

    def describe_not_learned(self) -> str:
        if not self.profile.not_learned:
            return "Every rule in the catalogue was derived from the evidence."
        lines = [
            f"{len(self.profile.not_learned)} rule(s) deliberately not derived:"
        ]
        lines.extend(
            f"  {entry.key}: {entry.reason}" for entry in self.profile.not_learned
        )
        return "\n".join(lines)


def learn(
    paths: list[str | Path],
    client: str,
    *,
    prompter: Prompter | None = None,
) -> LearnResult:
    """Learn a profile from one or more reference decks."""
    decks = [load_deck(path) for path in paths]
    if not decks:
        raise ValueError("at least one reference deck is required")
    return learn_from_decks(decks, client, prompter=prompter)


def learn_from_decks(
    decks: list[DeckModel],
    client: str,
    *,
    prompter: Prompter | None = None,
) -> LearnResult:
    """Learn from already-loaded decks.

    Multiple decks are handled by deriving from the first and merging the rest,
    which keeps one code path for ``learn A.pptx B.pptx`` and
    ``learn --add B.pptx``. The alternative -- pooling every observation across
    decks before classifying -- would make the support counts larger and the
    rules stronger, but it would also mean ``--add`` behaved differently from
    learning both at once, and a tool whose output depends on the order you fed
    it material is one nobody can reason about.
    """
    from tieout.learn.merge import merge

    primary = _derive_one(decks[0], client, prompter=prompter)
    for deck in decks[1:]:
        addition = _derive_one(deck, client, prompter=prompter)
        merged = merge(primary.profile, addition.profile)
        primary.profile = merged.profile
        primary.derivation.merge(addition.derivation)
    primary.decks = decks
    primary.reference_review = review_reference(decks, primary.profile)
    return primary


def review_reference(decks: list[DeckModel], profile: Profile) -> ReferenceReview:
    """Run the catalogue against the decks the profile was learned from.

    The tool's own acceptance criterion, made part of the command rather than
    left to a test. See :class:`ReferenceReview` for why the findings are
    reported rather than absorbed.

    Imported here rather than at module scope: the rules import the profile
    schema and the deck model, and pulling them in at the top of the learning
    package makes a cycle out of what is really a one-way dependency.
    """
    from tieout.rules.base import clear_caches, load_all_rules, run_rules

    load_all_rules()
    review = ReferenceReview()
    for deck in decks:
        clear_caches()
        review.findings.extend(run_rules(deck, profile).findings)
    clear_caches()
    return review


def _derive_one(
    deck: DeckModel, client: str, *, prompter: Prompter | None
) -> LearnResult:
    furniture = detect_furniture(deck, None)

    brand = derive_brand(deck, furniture)
    layout = derive_layout(deck, furniture)
    typography = derive_typography(deck, furniture)
    terms = derive_terms(deck, furniture)

    derivation = Derivation()
    for part in (brand.derivation, layout.derivation, typography.derivation, terms.derivation):
        derivation.merge(part)

    gutter, gutter_reason = derive_gutter(deck, furniture)
    if gutter is None:
        derivation.unlearned("layout.gutter_pt", gutter_reason)

    drafts: list[QuestionDraft] = [*derivation.questions, *hygiene_questions(deck)]
    interview = run_interview(drafts, prompter=prompter)

    profile = Profile(
        client=client,
        version=1,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d"),
        sources=[f"{deck.path.name} ({deck.slide_count} slides)"],
        slide=SlideProfile(width_pt=deck.width_pt, height_pt=deck.height_pt),
        archetypes={
            name: sorted(indices)
            for name, indices in sorted(deck.slides_by_archetype().items())
        },
        brand=brand.profile,
        layout=layout.profile,
        typography=typography.profile,
        hygiene=HygieneProfile(
            dictionary=list(terms.vocabulary),
            document_metadata_allowed=_reference_metadata(deck),
        ),
        rules=RulesProfile(disabled=list(DEFAULT_DISABLED_RULES)),
    )

    profile.typography.canon_terms = apply_canon_answers(terms, interview.answers)
    profile.typography.canon_accepted = {
        canonical: sorted(set(forms) - set(profile.typography.canon_terms.get(canonical, ())))
        for canonical, forms in terms.canon_accepted.items()
        if set(forms) - set(profile.typography.canon_terms.get(canonical, ()))
    }
    for path, value in apply_hygiene_answers(interview.answers).items():
        _set_path(profile, path, value)

    profile.not_learned = [
        NotLearned(key=key, reason=reason) for key, reason in derivation.not_learned
    ]
    profile.questions = list(interview.questions)
    profile.locks = list(interview.locks)
    profile.provenance = dict(derivation.provenance)
    profile.confidence = {
        key: value  # type: ignore[misc]
        for key, value in derivation.confidence.items()
        if value in ("high", "medium", "low")
    }

    _note_defaults(profile, derivation)
    _downgrade_defaulted_answers(profile, interview)
    return LearnResult(profile=profile, interview=interview, derivation=derivation)


def _reference_metadata(deck: DeckModel) -> list[str]:
    """The identifying docProps values the reference deck carries.

    These belong to the client whose approved deck this is, so HY-004 treats
    them as authorship rather than as a leak. Anything else in a later deck --
    a named individual, a counterparty, a codename -- still blocks. Without
    this the rule fires on the deck the profile was learned from, which tells a
    user only that TieOut cannot tell whose deck it is looking at.
    """
    values = {
        **deck.package.core.identifying_fields(),
        **deck.package.app.identifying_fields(),
    }
    return sorted({value.strip() for value in values.values() if value.strip()})


def _note_defaults(profile: Profile, derivation: Derivation) -> None:
    """Record provenance for the values that are defaults rather than derived.

    Section 14 requires every emitted value to carry provenance. For the hygiene
    settings the honest provenance is that they were not inferred at all, and
    saying so is what lets a reader tell a learned rule from a shipped default.
    """
    del derivation
    profile.set_provenance(
        "hygiene",
        "default, not inferred: nothing in the reference deck indicates whether "
        "notes, hidden slides or document metadata are acceptable",
        "medium",
    )
    if profile.hygiene.document_metadata_allowed:
        profile.set_provenance(
            "hygiene.document_metadata_allowed",
            "the identifying document properties the reference deck carries, "
            "recorded as the client's own so HY-004 reports only other names: "
            + ", ".join(profile.hygiene.document_metadata_allowed),
            "high",
        )
    profile.set_provenance(
        "hygiene.placeholder_markers",
        "default marker list, not inferred from the reference deck",
        "medium",
    )
    profile.set_provenance(
        "hygiene.min_image_dpi",
        "default 150 DPI, the conventional floor for print-quality artwork; not "
        "inferred",
        "medium",
    )
    profile.set_provenance(
        "brand.title_geometry_tolerance_pt",
        "default 2pt: a title is expected to sit where its own layout places it, "
        "which is not inferred from the reference deck",
        "medium",
    )
    profile.set_provenance(
        "rules.disabled",
        f"{', '.join(DEFAULT_DISABLED_RULES)} ship disabled: one needs a client "
        f"dictionary and the other needs the real font files to be worth reading. "
        f"Add a rule id to `rules.enabled` to switch it on",
        "medium",
    )
    profile.set_provenance(
        "layout.near_miss_alignment_pt",
        "default window, not inferred: below 0.5pt is indistinguishable from "
        "aligned and above 4pt is a different position rather than a mistake",
        "medium",
    )


def _downgrade_defaulted_answers(profile: Profile, interview: InterviewResult) -> None:
    """Mark any rule whose answer was defaulted as medium confidence.

    Section 8.4 requires this. A value the user never confirmed should not be
    presented with the same authority as one derived from twenty-three slides.
    """
    for question in interview.questions:
        if question.answered:
            continue
        current = profile.provenance.get(question.field_path, "")
        # Worded without reference to where it sits. This note is read in three
        # places -- beside the value in the YAML, in the House style tab, and as
        # the evidence under a finding -- and "the question above this field"
        # is only true in the first of them. In the review note it told a reader
        # to look above a finding for a question that is not there.
        note = (
            f"{current}; the question about it was not answered, so the default "
            f"was applied"
            if current
            else "no answer was given to the question about this field, so the "
            "default was applied"
        )
        profile.set_provenance(question.field_path, note, "medium")


def apply_answers(profile: Profile) -> tuple[list[str], list[str]]:
    """Fold answered questions back into the profile's fields.

    Returns ``(applied, recorded_only)`` as lists of field paths.

    Answering a question used to update only the question record: the answer,
    the lock and the provenance note were written, and the field the question
    was about kept the derived default. A profile could therefore say
    ``allow_speaker_notes: false`` directly under a note claiming the user had
    answered "yes, allow them", which is worse than not asking.

    Only the hygiene answers can be applied from the profile alone, because
    their mapping is a pure function of the answer text. The rest — a palette
    outlier, a logo variant, a terminology choice — are applied inside the
    derivers against evidence this function does not have, so they are reported
    as recorded-only rather than silently claimed. They are locked, so a
    re-learn will not overwrite the decision, and the field can be edited in the
    YAML directly, which is what the profile is for.
    """
    answers = {
        question.id: question.answer
        for question in profile.questions
        if question.answered and question.answer is not None
    }
    resolved = apply_hygiene_answers(answers)

    applied: list[str] = []
    for path, value in resolved.items():
        _set_path(profile, path, value)
        applied.append(path)

    recorded_only = [
        question.field_path
        for question in profile.questions
        if question.answered and question.field_path not in resolved
    ]
    return sorted(applied), sorted(set(recorded_only))


def _set_path(profile: Profile, path: str, value: object) -> None:
    parts = path.split(".")
    target: object = profile
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], value)
