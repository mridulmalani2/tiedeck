"""Rule infrastructure: the ABC, the finding record, the registry and the engine.

Design constraints from section 16, which are load-bearing rather than
stylistic:

* **Rules never import each other.** A rule that depends on another rule's
  output cannot be reasoned about, disabled, or tested in isolation.
* **Rules never touch python-pptx.** They read :class:`DeckModel`. If a rule
  needs something the model does not expose, the model gets extended.
* **Prefer fewer, higher-confidence findings.** A rule that reports ten
  instances of one underlying problem has failed even if all ten are true.
  Cluster related deviations into one finding.
* **Never silently skip anything.** A rule that cannot evaluate a shape records
  it in ``unchecked``; a rule that cannot run at all records why in
  ``rules_skipped``. Both reach the report. Silence that looks like a pass is
  the worst failure mode a QA tool has.
"""

from __future__ import annotations

import fnmatch
import weakref
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import ClassVar, Final

from tieout.model.deck import DeckModel, ShapeRef
from tieout.model.furniture import Furniture, detect_furniture
from tieout.profile.schema import Confidence, Profile, Severity, SuppressionFile

CATEGORIES: Final[tuple[str, ...]] = (
    "brand",
    "layout",
    "typography",
    "hygiene",
    "consistency",
    # Charts carry the argument in a banking deck, and the ways one fails are
    # not the ways a text box fails. Their expectations also come from the craft
    # rather than from the client's reference material -- see tieout/rules/chart.py
    # -- which is a second reason to keep them apart from the derived categories.
    "chart",
)

#: Categories whose findings are reported but do not drive the exit code.
#:
#: No rule in this package produces one — ``semantic`` belongs to the optional
#: ``tieout_review`` layer, whose findings come from a language model rather than
#: from arithmetic. They are worth reading and are not worth failing a build on
#: unasked, because a probabilistic finding gating a deterministic gate would
#: make the exit code mean something different from one run to the next. The
#: constant lives here so the contract is stated once, next to ``exceeds``.
NON_GATING_CATEGORIES: Final[frozenset[str]] = frozenset({"semantic"})

#: Ordering for report grouping and for ``--fail-on`` comparisons.
SEVERITY_ORDER: Final[dict[str, int]] = {
    "blocker": 0,
    "major": 1,
    "minor": 2,
    "info": 3,
}

SEVERITY_GLYPHS: Final[dict[str, str]] = {
    "blocker": "●",
    "major": "◆",
    "minor": "▲",
    "info": "·",
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One reported deviation.

    ``where`` is a :class:`ShapeRef` when the finding is about a shape and a
    plain slide index when it is about a slide or the deck. ``measured`` and
    ``expected`` are pre-formatted strings rather than numbers because the unit
    and the precision are part of the message: "872.0pt" and "872pt" read
    differently to a person deciding whether to care.
    """

    rule_id: str
    category: str
    severity: Severity
    confidence: Confidence
    where: ShapeRef | int
    message: str
    measured: str | None = None
    expected: str | None = None
    expected_provenance: str | None = None
    bbox_pt: tuple[float, float, float, float] | None = None
    #: What to actually do about it, in the imperative, where the rule knows.
    #: A finding that states a deviation and stops leaves the reader to work
    #: out the fix from a Delta-E figure; the rule has usually already computed
    #: the answer. Empty where there is no single mechanical fix -- an
    #: overlapping pair or a total that does not sum is a judgement, and a
    #: confident instruction there would be worse than silence.
    remedy: str | None = None

    @property
    def slide_index(self) -> int:
        return self.where.slide_index if isinstance(self.where, ShapeRef) else self.where

    @property
    def shape_name(self) -> str | None:
        return self.where.display_name if isinstance(self.where, ShapeRef) else None

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        return (
            self.slide_index,
            SEVERITY_ORDER.get(self.severity, 9),
            self.rule_id,
            self.shape_name or "",
        )

    @property
    def delta(self) -> str | None:
        """``measured -> expected``, for the console and HTML reports."""
        if self.measured is None and self.expected is None:
            return None
        return f"{self.measured or '-'} → {self.expected or '-'}"


@dataclass(frozen=True, slots=True)
class Unchecked:
    """A shape a rule could not evaluate, and why.

    LO-006 is the motivating case: text overflow cannot be measured without the
    actual font file, and guessing would produce confident nonsense. Recording
    the skip is the honest alternative.
    """

    rule_id: str
    where: ShapeRef | int
    reason: str

    @property
    def slide_index(self) -> int:
        return self.where.slide_index if isinstance(self.where, ShapeRef) else self.where


@dataclass(frozen=True, slots=True)
class RuleSkipped:
    """A rule that did not run, and why.

    ``failed`` separates the two reasons a rule can be silent, which a reader
    must never have to guess between. A rule that declines because the profile
    holds no expectation for it has done its job; a rule that raised has told
    you nothing at all, and a report counting it among the rules that ran is
    claiming coverage it does not have.
    """

    rule_id: str
    reason: str
    failed: bool = False


class Rule(ABC):
    """Base class for every rule.

    Subclasses set the class variables and implement :meth:`run`. The docstring
    is not decoration: section 9 requires each rule to state exactly what it
    measures and its known false-positive mode, and both are surfaced by
    ``tieout rules``.
    """

    id: ClassVar[str] = ""
    category: ClassVar[str] = ""
    severity: ClassVar[Severity] = "minor"
    confidence: ClassVar[Confidence] = "high"
    default_enabled: ClassVar[bool] = True
    #: One line for ``tieout rules``.
    summary: ClassVar[str] = ""
    #: The profile paths this rule reads. Used by ``tieout rules`` to explain
    #: where a rule's expectations come from, and to skip a rule whose inputs
    #: were never learned.
    requires: ClassVar[tuple[str, ...]] = ()

    def __init__(self) -> None:
        self.unchecked: list[Unchecked] = []
        self.skipped_reason: str | None = None

    # -- the contract ------------------------------------------------------------

    @abstractmethod
    def run(self, deck: DeckModel, profile: Profile) -> list[Finding]:
        """Evaluate the deck and return findings."""

    # -- helpers available to every rule -----------------------------------------

    def furniture(self, deck: DeckModel, profile: Profile) -> Furniture:
        """Logo, page-number and boilerplate identification for this deck.

        Memoised per deck and profile, because a dozen rules ask for it and
        re-deriving it each time would walk every shape on every slide again.
        """
        return furniture_for(deck, profile)

    def finding(
        self,
        *,
        where: ShapeRef | int,
        message: str,
        profile: Profile | None = None,
        provenance_path: str | None = None,
        measured: str | None = None,
        expected: str | None = None,
        bbox_pt: tuple[float, float, float, float] | None = None,
        severity: Severity | None = None,
        confidence: Confidence | None = None,
        remedy: str | None = None,
    ) -> Finding:
        """Build a finding, pulling severity, confidence and provenance from the
        profile so a rule cannot forget to.

        A finding whose expectation came from the client's own reference deck and
        does not say so is a finding the user has no reason to believe.
        """
        resolved_severity = severity or (
            profile.severity_of(self.id, self.severity) if profile else self.severity
        )
        resolved_confidence = confidence or self.confidence
        provenance: str | None = None
        if profile is not None and provenance_path is not None:
            provenance = profile.provenance_for(provenance_path)
            if confidence is None:
                resolved_confidence = _weaker(
                    resolved_confidence, profile.confidence_for(provenance_path)
                )
        return Finding(
            rule_id=self.id,
            category=self.category,
            severity=resolved_severity,
            confidence=resolved_confidence,
            where=where,
            message=message,
            measured=measured,
            expected=expected,
            expected_provenance=provenance,
            bbox_pt=bbox_pt,
            remedy=remedy,
        )

    def note_unchecked(self, where: ShapeRef | int, reason: str) -> None:
        self.unchecked.append(Unchecked(rule_id=self.id, where=where, reason=reason))

    def skip(self, reason: str) -> list[Finding]:
        """Record that the rule cannot run and return no findings."""
        self.skipped_reason = reason
        return []

    def archetype_of(self, deck: DeckModel, slide_index: int) -> str:
        slide = deck.slide(slide_index)
        return slide.archetype if slide else "unknown"


def _weaker(a: Confidence, b: Confidence) -> Confidence:
    order: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
    return a if order[a] >= order[b] else b


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------

REGISTRY: dict[str, type[Rule]] = {}


def register(cls: type[Rule]) -> type[Rule]:
    """Register a rule class by id. Duplicate ids are a programming error."""
    if not cls.id:
        raise ValueError(f"{cls.__name__} has no rule id")
    if cls.category not in CATEGORIES:
        raise ValueError(f"{cls.id}: unknown category {cls.category!r}")
    if cls.id in REGISTRY:
        raise ValueError(f"duplicate rule id {cls.id} ({cls.__name__})")
    REGISTRY[cls.id] = cls
    return cls


def load_all_rules() -> dict[str, type[Rule]]:
    """Import every rule module so the registry is populated.

    Imported here rather than in ``tieout/__init__`` so that importing the model
    layer does not drag in every rule, which keeps ``test_no_network``'s module
    walk and the CLI's start-up time honest.
    """
    from tieout.rules import (  # noqa: F401
        brand,
        chart,
        consistency,
        hygiene,
        layout,
        typography,
    )

    return dict(REGISTRY)


def rules_in_category(category: str) -> list[type[Rule]]:
    load_all_rules()
    return [cls for cls in REGISTRY.values() if cls.category == category]


def select_rules(
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
) -> list[type[Rule]]:
    """Resolve ``--rules`` and ``--exclude`` patterns to rule classes.

    Patterns are glob style, so ``BR-*`` selects every brand rule and ``LO-003``
    selects one. Selection order is always rule id order, so two runs of the
    same command produce findings in the same sequence.
    """
    load_all_rules()
    chosen = sorted(REGISTRY.values(), key=lambda cls: cls.id)
    if include:
        patterns = [p.strip() for p in include if p.strip()]
        chosen = [
            cls
            for cls in chosen
            if any(fnmatch.fnmatch(cls.id, pattern) for pattern in patterns)
        ]
    if exclude:
        patterns = [p.strip() for p in exclude if p.strip()]
        chosen = [
            cls
            for cls in chosen
            if not any(fnmatch.fnmatch(cls.id, pattern) for pattern in patterns)
        ]
    return chosen


# --------------------------------------------------------------------------------------
# Furniture memoisation
# --------------------------------------------------------------------------------------

#: Deck -> (profile, furniture). The deck is held weakly so a long-running
#: process does not retain every deck it has ever audited; the profile is held
#: strongly, which is what makes the identity check below sound.
#:
#: Keying on ``id()`` would be a real bug rather than a theoretical one: CPython
#: recycles object addresses, so a short-lived profile can be allocated at the
#: address of a collected one and silently inherit its furniture.
_FURNITURE_CACHE: weakref.WeakKeyDictionary[DeckModel, tuple[Profile, Furniture]] = (
    weakref.WeakKeyDictionary()
)


def furniture_for(deck: DeckModel, profile: Profile) -> Furniture:
    """Logo, page-number and boilerplate identification, memoised per deck."""
    cached = _FURNITURE_CACHE.get(deck)
    if cached is not None and cached[0] is profile:
        return cached[1]
    furniture = detect_furniture(deck, profile)
    _FURNITURE_CACHE[deck] = (profile, furniture)
    return furniture


def clear_caches() -> None:
    """Drop memoised state, for tests and for a process auditing many decks."""
    _FURNITURE_CACHE.clear()


# --------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------


@dataclass
class AuditResult:
    """Everything one ``tieout check`` produced."""

    deck_path: str
    client: str
    profile_version: int
    generated_at: str
    findings: list[Finding] = field(default_factory=list)
    unchecked: list[Unchecked] = field(default_factory=list)
    rules_skipped: list[RuleSkipped] = field(default_factory=list)
    rules_run: list[str] = field(default_factory=list)
    suppressed: list[Finding] = field(default_factory=list)
    slide_count: int = 0

    @property
    def summary(self) -> dict[str, int]:
        counts = dict.fromkeys(SEVERITY_ORDER, 0)
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        counts["total"] = len(self.findings)
        return counts

    def findings_by_slide(self) -> dict[int, list[Finding]]:
        out: dict[int, list[Finding]] = {}
        for finding in sorted(self.findings, key=lambda f: f.sort_key):
            out.setdefault(finding.slide_index, []).append(finding)
        return out

    @property
    def failed_rules(self) -> list[RuleSkipped]:
        """Rules that raised rather than declining to run.

        A rule that crashed has told you nothing about the deck, so an audit
        containing one is incomplete rather than clean. Separated from the
        rules that declined -- disabled, or missing a profile field -- because
        those are decisions and this is a fault.
        """
        return [entry for entry in self.rules_skipped if entry.failed]

    @property
    def complete(self) -> bool:
        """Whether every selected rule actually ran."""
        return not self.failed_rules

    def worst_severity(self) -> str | None:
        if not self.findings:
            return None
        return min(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9)).severity

    def exceeds(self, threshold: str, *, include_non_gating: bool = False) -> bool:
        """Whether any finding is at or above ``threshold``. Drives the exit code.

        Findings in :data:`NON_GATING_CATEGORIES` are excluded unless the caller
        opts in, so adding the optional semantic layer to a run cannot change
        what an existing gate does.
        """
        limit = SEVERITY_ORDER.get(threshold, 0)
        return any(
            SEVERITY_ORDER.get(f.severity, 9) <= limit
            and (include_non_gating or f.category not in NON_GATING_CATEGORIES)
            for f in self.findings
        )


def run_rules(
    deck: DeckModel,
    profile: Profile,
    *,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    suppressions: SuppressionFile | None = None,
    min_severity: str | None = None,
) -> AuditResult:
    """Run the selected rules against a deck and collect everything they produced.

    A rule that raises is reported as skipped with the exception text rather than
    being allowed to abort the audit. One malformed chart part should not stop a
    user from seeing the other thirty-five rules' findings.
    """
    result = AuditResult(
        deck_path=str(deck.path),
        client=profile.client,
        profile_version=profile.version,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        slide_count=deck.slide_count,
    )

    named = _explicitly_named(include)
    for rule_cls in select_rules(include, exclude):
        # Naming a rule exactly on the command line is a request to run it, which
        # is the only way a user can reach LO-006 or TY-009 without editing their
        # profile. A glob such as "LO-*" is a filter, not an opt-in, so it leaves
        # the default alone.
        default_enabled = rule_cls.default_enabled or rule_cls.id in named
        if not profile.rule_enabled(rule_cls.id, default=default_enabled):
            reason = (
                "disabled in the profile"
                if rule_cls.id in profile.rules.disabled
                else "disabled by default"
            )
            result.rules_skipped.append(RuleSkipped(rule_cls.id, reason))
            continue

        missing = _missing_requirements(rule_cls, profile)
        if missing:
            result.rules_skipped.append(
                RuleSkipped(
                    rule_cls.id,
                    "the profile does not define " + ", ".join(missing),
                )
            )
            continue

        rule = rule_cls()
        try:
            findings = rule.run(deck, profile)
        except Exception as exc:
            result.rules_skipped.append(
                RuleSkipped(
                    rule_cls.id,
                    f"raised {type(exc).__name__}: {exc}",
                    failed=True,
                )
            )
            continue

        if rule.skipped_reason is not None:
            result.rules_skipped.append(RuleSkipped(rule_cls.id, rule.skipped_reason))

        result.rules_run.append(rule_cls.id)
        result.unchecked.extend(rule.unchecked)

        for finding in findings:
            shape_name = (
                finding.where.name if isinstance(finding.where, ShapeRef) else None
            )
            if suppressions is not None and suppressions.matches(
                finding.rule_id, finding.slide_index, shape_name
            ):
                result.suppressed.append(finding)
                continue
            result.findings.append(finding)

    if min_severity is not None:
        limit = SEVERITY_ORDER.get(min_severity, 3)
        result.findings = [
            f for f in result.findings if SEVERITY_ORDER.get(f.severity, 9) <= limit
        ]

    _locate(result.findings, deck)
    result.findings.sort(key=lambda f: f.sort_key)
    result.rules_skipped.sort(key=lambda s: s.rule_id)
    result.unchecked.sort(key=lambda u: (u.slide_index, u.rule_id))
    return result


def _locate(findings: list[Finding], deck: DeckModel) -> None:
    """Give every finding that names a shape the box that shape occupies.

    A rule that aggregates -- one wrong colour used by six shapes, one typeface
    across four runs -- anchors its finding on the first of them and mostly did
    not think to pass a box along with it. That was invisible while nothing drew
    the box; now that the page outlines the shape a finding is about, a finding
    without one is a finding the reader cannot be shown.

    Resolved here, once, rather than at forty-two call sites. The rule already
    said which shape it means; asking the deck where that shape is needs no help
    from the rule, and a rule added later gets this for free.
    """
    boxes: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for slide in deck.slides:
        for shape in slide.all_shapes():
            if shape.bbox_pt is not None:
                boxes[(slide.index, shape.ref.shape_id)] = shape.bbox_pt

    for index, finding in enumerate(findings):
        if finding.bbox_pt is not None or not isinstance(finding.where, ShapeRef):
            continue
        box = boxes.get((finding.where.slide_index, finding.where.shape_id))
        if box is not None:
            findings[index] = replace(finding, bbox_pt=box)


def _explicitly_named(include: Sequence[str] | None) -> frozenset[str]:
    """Include patterns that name one rule exactly rather than matching a set."""
    if not include:
        return frozenset()
    return frozenset(
        pattern.strip()
        for pattern in include
        if pattern.strip() and not set(pattern) & {"*", "?", "[", "]"}
    )


def _missing_requirements(rule_cls: type[Rule], profile: Profile) -> list[str]:
    """Profile paths a rule needs that were never learned.

    A rule whose expectation does not exist must not run, because the only
    findings it could produce would be measured against a default the client
    never agreed to.
    """
    missing: list[str] = []
    not_learned = profile.not_learned_keys()
    for path in rule_cls.requires:
        if path in not_learned:
            missing.append(path)
            continue
        if _resolve_path(profile, path) in (None, [], {}, ""):
            missing.append(path)
    return missing


def _resolve_path(profile: Profile, path: str) -> object:
    current: object = profile
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def cluster_findings(
    findings: Iterable[Finding], *, key: str = "rule_id"
) -> list[Finding]:
    """Collapse findings that share a slide and a rule into one.

    Section 16: a row of five misaligned cards is one problem, and reporting it
    five times trains the user to skim past the report. Rules do their own
    clustering where they can be smarter about it; this is the backstop.
    """
    grouped: dict[tuple[int, str], list[Finding]] = {}
    for finding in findings:
        grouped.setdefault((finding.slide_index, getattr(finding, key)), []).append(
            finding
        )

    out: list[Finding] = []
    for group in grouped.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        first = group[0]
        others = len(group) - 1
        # ``replace`` rather than a field-by-field rebuild. The rebuild listed
        # eight fields and silently dropped the ninth when one was added, so a
        # clustered finding lost its remedy while an unclustered one kept it --
        # the same defect, reported with a fix on one slide and without on the
        # next. Naming only what changes cannot go stale.
        out.append(
            replace(
                first,
                severity=min(
                    (f.severity for f in group),
                    key=lambda s: SEVERITY_ORDER.get(s, 9),
                ),
                message=f"{first.message} (and {others} more on this slide)",
            )
        )
    return sorted(out, key=lambda f: f.sort_key)
