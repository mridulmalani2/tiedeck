"""Observation classification: invariant, dominant, multimodal or variable.

This is the judgement layer. Given every observation of one key at one scope, it
decides whether there is a rule there at all, and with what confidence.

The governing principle from section 16 is that the engine must never invent a
rule it cannot justify from evidence. Silence is correct. A tool that derives a
confident rule from two observations will produce a profile full of assertions
the client never made, and the first deck checked against it will be covered in
findings that are the tool's fault. So :class:`ObservationClass.VARIABLE` is a
perfectly good outcome, and it is recorded with a reason rather than hidden.

Classification per section 8.2:

===============  =========================================================
INVARIANT        one distinct value, support >= min_support
DOMINANT         top value >= 85% of weighted observations, support ok
MULTIMODAL       two or three clusters each >= 15%
VARIABLE         no structure, or entropy above threshold
===============  =========================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from tieout.cluster import (
    CategoricalDominance,
    Cluster,
    Dominance,
    categorical_dominance,
    derive_tolerance,
    dominance,
)
from tieout.learn.observe import Observation

#: Minimum weighted share for the top value to count as dominant.
DOMINANT_SHARE: Final[float] = 0.85

#: Minimum weighted share for a cluster to count as one mode of a multimodal key.
MODE_SHARE: Final[float] = 0.15

#: At most this many modes before a key is noise rather than a set of conventions.
MAX_MODES: Final[int] = 3

#: Above this many bits of entropy there is no convention to find.
ENTROPY_CEILING: Final[float] = 1.6

#: Minimum observation counts, per section 8.2.
MIN_SUPPORT_DECK: Final[int] = 3
MIN_SUPPORT_ARCHETYPE: Final[int] = 2
MIN_SUPPORT_STRUCTURAL: Final[int] = 1

#: Clustering tolerances, per section 8.2.
TOLERANCE_POSITION_PT: Final[float] = 2.0
TOLERANCE_FONT_SIZE_PT: Final[float] = 0.0
TOLERANCE_DELTA_E: Final[float] = 3.0

#: Tolerance floors for derived rule tolerances, per section 8.2.
FLOOR_POSITION_PT: Final[float] = 2.0
FLOOR_SIZE_SHARE: Final[float] = 0.03


class ObservationClass(Enum):
    INVARIANT = "invariant"
    DOMINANT = "dominant"
    MULTIMODAL = "multimodal"
    VARIABLE = "variable"


@dataclass
class Classification:
    """What the engine concluded about one ``(key, scope)`` group."""

    key: str
    scope: str
    observation_class: ObservationClass
    #: The value to emit. None for VARIABLE.
    value: Any = None
    #: For MULTIMODAL, every permitted value.
    allowed: list[Any] = field(default_factory=list)
    confidence: str = "high"
    support: int = 0
    total_weight: float = 0.0
    top_share: float = 0.0
    entropy: float = 0.0
    spread: float = 0.0
    #: Observations that disagree with the emitted value, for the interview.
    outliers: list[Observation] = field(default_factory=list)
    #: Why nothing was emitted, for ``not_learned``.
    reason: str = ""
    slides: tuple[int, ...] = ()

    @property
    def learned(self) -> bool:
        return self.observation_class is not ObservationClass.VARIABLE

    @property
    def has_outliers(self) -> bool:
        return bool(self.outliers)

    def tolerance(self, *, floor: float) -> float:
        """The rule tolerance implied by the observed spread."""
        return derive_tolerance(self.spread, floor=floor)

    def provenance(self, *, total_slides: int | None = None) -> str:
        """The provenance sentence for this value.

        Written here rather than at emission so the numbers cannot drift away
        from the evidence that produced them.
        """
        slide_count = len(set(self.slides))
        match self.observation_class:
            case ObservationClass.INVARIANT:
                if total_slides is not None:
                    return (
                        f"observed identically on {slide_count} of {total_slides} "
                        f"slides, invariant"
                    )
                return f"observed identically across {self.support} observations, invariant"
            case ObservationClass.DOMINANT:
                return (
                    f"dominant on {self.top_share:.1%} of {self.support} weighted "
                    f"observations across {slide_count} slides"
                )
            case ObservationClass.MULTIMODAL:
                return (
                    f"{len(self.allowed)} distinct values across {self.support} "
                    f"observations on {slide_count} slides, none dominant"
                )
            case _:
                return self.reason or "not learned"


def min_support_for(scope: str, *, structural: bool = False) -> int:
    """Minimum observations required at a given scope."""
    if structural:
        return MIN_SUPPORT_STRUCTURAL
    if scope.startswith("archetype:") or scope.startswith("table:"):
        return MIN_SUPPORT_ARCHETYPE
    return MIN_SUPPORT_DECK


def classify_numeric(
    key: str,
    scope: str,
    observations: list[Observation],
    *,
    tolerance: float = TOLERANCE_POSITION_PT,
    min_support: int | None = None,
    structural: bool = False,
) -> Classification:
    """Classify continuous observations, clustering first.

    Continuous values must be clustered before classification or nothing is ever
    invariant: a logo placed by hand at 871.9pt and 872.1pt is at one position,
    and treating those as two values would make every real deck multimodal.
    """
    required = min_support if min_support is not None else min_support_for(
        scope, structural=structural
    )
    values = [float(o.value) for o in observations]
    weights = [o.weight for o in observations]
    slides = tuple(o.slide_index for o in observations)

    if not values:
        return Classification(
            key=key,
            scope=scope,
            observation_class=ObservationClass.VARIABLE,
            reason="no observations",
            slides=slides,
        )

    result = dominance(values, tolerance, weights)
    return _classify_from_dominance(
        key=key,
        scope=scope,
        observations=observations,
        result=result,
        required=required,
        slides=slides,
        tolerance=tolerance,
    )


def _classify_from_dominance(
    *,
    key: str,
    scope: str,
    observations: list[Observation],
    result: Dominance,
    required: int,
    slides: tuple[int, ...],
    tolerance: float,
) -> Classification:
    support = result.total_support
    base = Classification(
        key=key,
        scope=scope,
        observation_class=ObservationClass.VARIABLE,
        support=support,
        total_weight=result.total_weight,
        top_share=result.top_share,
        entropy=result.entropy,
        slides=slides,
    )

    if support < required:
        base.reason = (
            f"only {support} observation{'s' if support != 1 else ''}, "
            f"below min_support of {required}"
        )
        return base

    top = result.top
    if top is None:  # pragma: no cover - a non-empty dominance always has a top
        base.reason = "no clusters formed"
        return base

    if result.distinct == 1:
        base.observation_class = ObservationClass.INVARIANT
        base.value = top.mode
        base.confidence = "high"
        base.spread = top.spread
        base.allowed = [top.mode]
        return base

    if result.top_share >= DOMINANT_SHARE:
        base.observation_class = ObservationClass.DOMINANT
        base.value = top.mode
        base.confidence = "medium"
        base.spread = top.spread
        base.allowed = [top.mode]
        base.outliers = _outliers_outside(observations, top, tolerance)
        return base

    modes = [c for c in result.clusters if c.weight / result.total_weight >= MODE_SHARE]
    if 2 <= len(modes) <= MAX_MODES and result.entropy <= ENTROPY_CEILING:
        base.observation_class = ObservationClass.MULTIMODAL
        base.allowed = [c.mode for c in modes]
        base.value = top.mode
        base.confidence = "medium"
        base.spread = max(c.spread for c in modes)
        return base

    base.reason = (
        f"no consistent value found: {result.distinct} distinct values across "
        f"{support} observations, top share {result.top_share:.0%}, "
        f"entropy {result.entropy:.2f} bits"
    )
    return base


def _outliers_outside(
    observations: list[Observation], cluster: Cluster, tolerance: float
) -> list[Observation]:
    """Observations that fall outside the dominant cluster.

    These become the interview's highest-value question: "the logo sits at
    x=872pt on 23 slides but at x=840pt on slides 7 and 19 -- exceptions, or
    errors in the reference deck?"
    """
    return [
        observation
        for observation in observations
        if abs(float(observation.value) - cluster.centre) > max(tolerance, 1e-9)
    ]


def classify_categorical(
    key: str,
    scope: str,
    observations: list[Observation],
    *,
    min_support: int | None = None,
    structural: bool = False,
) -> Classification:
    """Classify discrete observations -- typefaces, conventions, format strings.

    Categorical values cannot be clustered: "curly" and "straight" are not two
    approximations of the same convention.
    """
    required = min_support if min_support is not None else min_support_for(
        scope, structural=structural
    )
    slides = tuple(o.slide_index for o in observations)
    tallied: CategoricalDominance = categorical_dominance(
        [(str(o.value), o.weight) for o in observations]
    )

    base = Classification(
        key=key,
        scope=scope,
        observation_class=ObservationClass.VARIABLE,
        support=tallied.total_support,
        total_weight=tallied.total_weight,
        top_share=tallied.top_share,
        entropy=tallied.entropy,
        slides=slides,
    )

    if tallied.total_support < required:
        base.reason = (
            f"only {tallied.total_support} observation"
            f"{'s' if tallied.total_support != 1 else ''}, "
            f"below min_support of {required}"
        )
        return base

    top = tallied.top
    if top is None:  # pragma: no cover
        base.reason = "no observations"
        return base

    if tallied.distinct == 1:
        base.observation_class = ObservationClass.INVARIANT
        base.value = top[0]
        base.allowed = [top[0]]
        base.confidence = "high"
        return base

    if tallied.top_share >= DOMINANT_SHARE:
        base.observation_class = ObservationClass.DOMINANT
        base.value = top[0]
        base.allowed = [top[0]]
        base.confidence = "medium"
        base.outliers = [o for o in observations if str(o.value) != top[0]]
        return base

    modes = [
        value for value, _ in tallied.ordered if tallied.share_of(value) >= MODE_SHARE
    ]
    if 2 <= len(modes) <= MAX_MODES and tallied.entropy <= ENTROPY_CEILING:
        base.observation_class = ObservationClass.MULTIMODAL
        base.allowed = modes
        base.value = top[0]
        base.confidence = "medium"
        return base

    base.reason = (
        f"no consistent value found: {tallied.distinct} distinct values across "
        f"{tallied.total_support} observations, top share {tallied.top_share:.0%}, "
        f"entropy {tallied.entropy:.2f} bits"
    )
    return base


def explain_by_archetype(
    key: str,
    per_archetype: dict[str, Classification],
) -> bool:
    """Whether a multimodal deck-wide key is explained by archetype.

    Section 8.2 requires attempting this before falling back to an enumerated
    allowed set. It succeeds when every archetype that has enough evidence
    resolved to a single value -- which is precisely the situation the archetype
    machinery exists to detect. "The logo is at two positions" becomes "the logo
    is top-right on content slides and centred on dividers", and the second is a
    rule worth having.
    """
    resolved = [
        c
        for c in per_archetype.values()
        if c.observation_class
        in (ObservationClass.INVARIANT, ObservationClass.DOMINANT)
    ]
    if len(resolved) < 2:
        return False
    distinct = {_value_key(c.value) for c in resolved}
    return len(distinct) >= 2 and len(resolved) == len(
        [c for c in per_archetype.values() if c.support > 0]
    )


def _value_key(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, (list, tuple)):
        return tuple(_value_key(v) for v in value)
    return value


# --------------------------------------------------------------------------------------
# Derivation bookkeeping
# --------------------------------------------------------------------------------------


@dataclass
class QuestionDraft:
    """A question a deriver wants the interview to consider asking.

    Drafted rather than asked, because the interview ranks every draft by impact
    and keeps only the twelve that matter. A deriver that asked directly would
    let a low-value question about a footnote crowd out a high-value one about
    the logo.
    """

    id: str
    field_path: str
    question: str
    options: list[str] = field(default_factory=list)
    default: str = ""
    #: Number of future findings that would depend on the answer. Drives ranking.
    impact: int = 0
    kind: str = "generic"
    #: Slides that prompted the question, so it can be asked concretely.
    slides: tuple[int, ...] = ()


@dataclass
class Derivation:
    """Accumulator shared by every deriver.

    Carries the provenance and confidence for each emitted field, the keys that
    were deliberately not learned, and the questions the derivers would like
    asked. Section 16: if the provenance comment cannot be written, the value
    must not be emitted -- so provenance is recorded at the moment of derivation,
    not reconstructed later.
    """

    provenance: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, str] = field(default_factory=dict)
    not_learned: list[tuple[str, str]] = field(default_factory=list)
    questions: list[QuestionDraft] = field(default_factory=list)

    def note(self, field_path: str, text: str, confidence: str = "high") -> None:
        self.provenance[field_path] = text
        self.confidence[field_path] = confidence

    def note_from(self, field_path: str, classification: Classification,
                  *, total_slides: int | None = None) -> None:
        """Record provenance straight from a classification."""
        self.note(
            field_path,
            classification.provenance(total_slides=total_slides),
            classification.confidence,
        )

    def unlearned(self, key: str, reason: str) -> None:
        if not any(existing == key for existing, _ in self.not_learned):
            self.not_learned.append((key, reason))

    def ask(self, draft: QuestionDraft) -> None:
        self.questions.append(draft)

    def merge(self, other: Derivation) -> None:
        self.provenance.update(other.provenance)
        self.confidence.update(other.confidence)
        for key, reason in other.not_learned:
            self.unlearned(key, reason)
        self.questions.extend(other.questions)
