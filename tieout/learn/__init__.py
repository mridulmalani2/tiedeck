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
class LearnResult:
    """A learned profile and an account of how it was produced."""

    profile: Profile
    interview: InterviewResult = field(default_factory=InterviewResult)
    derivation: Derivation = field(default_factory=Derivation)
    decks: list[DeckModel] = field(default_factory=list)

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
    return primary


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
        hygiene=HygieneProfile(),
        rules=RulesProfile(disabled=list(DEFAULT_DISABLED_RULES)),
    )

    profile.typography.canon_terms = apply_canon_answers(terms, interview.answers)
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
        note = (
            f"{current}; the question above it was not answered, so the default "
            f"was applied"
            if current
            else "defaulted: the question above this field was not answered"
        )
        profile.set_provenance(question.field_path, note, "medium")


def _set_path(profile: Profile, path: str, value: object) -> None:
    parts = path.split(".")
    target: object = profile
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], value)
