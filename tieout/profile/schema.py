"""The learned profile schema.

A profile is the whole contract between the learning engine and the rules. It is
also a document a user reads, edits and argues with, which is why every derived
value carries a provenance string: "expected 872pt" is arbitrary, "expected 872pt,
learned from 18 of 26 slides in your reference deck" is a fact the user can check.

Validated with pydantic so a hand-edited profile fails loudly at load rather than
silently at rule-evaluation time.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SEVERITIES: Final[tuple[str, ...]] = ("blocker", "major", "minor", "info")
CONFIDENCES: Final[tuple[str, ...]] = ("high", "medium", "low")

Severity = Literal["blocker", "major", "minor", "info"]
Confidence = Literal["high", "medium", "low"]

#: Sentinel meaning "this archetype legitimately has no logo".
EXEMPT: Final[str] = "exempt"


class _Model(BaseModel):
    """Base with strict field checking, so a typo in a profile is an error."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Box(_Model):
    """A rectangle in points, with the tolerance its rule should allow."""

    left: float
    top: float
    width: float
    height: float
    tolerance_pt: float = 2.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.left, self.top, self.width, self.height)

    def describe(self) -> str:
        return (
            f"left {self.left:g}pt, top {self.top:g}pt, "
            f"{self.width:g}x{self.height:g}pt (+/-{self.tolerance_pt:g}pt)"
        )


class Margins(_Model):
    """Safe margins in points, measured inward from each edge."""

    top: float
    right: float
    bottom: float
    left: float


class SlideProfile(_Model):
    """Canvas dimensions. Invariant by construction, so tolerance is tight."""

    width_pt: float
    height_pt: float
    tolerance_pt: float = 0.5


class FontRole(_Model):
    """The permitted sizes for one text role.

    Either an exact allowed set or an inclusive band, never both. A role with
    few distinct observed sizes gets an exact set, because a band invented from
    two observations would licence sizes the client has never used.
    """

    exact_pt: list[float] | None = None
    min_pt: float | None = None
    max_pt: float | None = None

    @field_validator("exact_pt")
    @classmethod
    def _sorted_unique(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return None
        return sorted(set(value))

    def permits(self, size_pt: float, tolerance_pt: float = 0.01) -> bool:
        if self.exact_pt is not None:
            return any(abs(size_pt - allowed) <= tolerance_pt for allowed in self.exact_pt)
        if self.min_pt is not None and size_pt < self.min_pt - tolerance_pt:
            return False
        if self.max_pt is not None and size_pt > self.max_pt + tolerance_pt:
            return False
        return self.min_pt is not None or self.max_pt is not None

    def describe(self) -> str:
        if self.exact_pt is not None:
            return "one of " + ", ".join(f"{v:g}pt" for v in self.exact_pt)
        if self.min_pt is not None and self.max_pt is not None:
            return f"{self.min_pt:g}pt to {self.max_pt:g}pt"
        if self.min_pt is not None:
            return f"at least {self.min_pt:g}pt"
        if self.max_pt is not None:
            return f"at most {self.max_pt:g}pt"
        return "unconstrained"

    @property
    def is_empty(self) -> bool:
        return self.exact_pt is None and self.min_pt is None and self.max_pt is None


class FontsProfile(_Model):
    """The approved typefaces and the size bands for each role."""

    allowed: list[str] = Field(default_factory=list)
    roles: dict[str, FontRole] = Field(default_factory=dict)

    def permits_name(self, name: str | None) -> bool:
        if name is None:
            return True
        return any(name.casefold() == allowed.casefold() for allowed in self.allowed)


class LogoProfile(_Model):
    """Logo identity and its expected box per archetype.

    ``per_archetype`` maps an archetype either to a :class:`Box` or to the string
    ``exempt``, which records that the logo is deliberately absent there. An
    archetype missing from the mapping is not checked at all -- the difference
    matters, because "absent by design" and "no evidence either way" should not
    produce the same finding.
    """

    image_sha1: list[str] = Field(default_factory=list)
    per_archetype: dict[str, Box | Literal["exempt"]] = Field(default_factory=dict)
    aspect_ratio_tolerance: float = 0.01
    size_tolerance_pt: float = 2.0

    def box_for(self, archetype: str) -> Box | None:
        entry = self.per_archetype.get(archetype)
        return entry if isinstance(entry, Box) else None

    def is_exempt(self, archetype: str) -> bool:
        return self.per_archetype.get(archetype) == EXEMPT

    def is_known(self, archetype: str) -> bool:
        return archetype in self.per_archetype


class PageNumberProfile(_Model):
    """Where page numbers go, what they look like, and where they are required."""

    required_on: list[str] = Field(default_factory=list)
    regex: str = r"^\d+$"
    box_pt: Box | None = None
    must_ascend: bool = True


class BoilerplateEntry(_Model):
    """A text string the house style requires on certain archetypes."""

    text: str
    required_on: list[str] = Field(default_factory=list)
    #: Set when the string was recognised as a confidentiality marking, which the
    #: interview treats as a higher-stakes question than a generic footer.
    is_confidentiality: bool = False
    #: Matching is normalised (case, whitespace, quote style) because a footer
    #: retyped by hand differs from the original in ways no reader would notice.
    match_normalised: bool = True


class FooterProfile(_Model):
    page_number: PageNumberProfile | None = None
    boilerplate: list[BoilerplateEntry] = Field(default_factory=list)


class BrandProfile(_Model):
    fonts: FontsProfile = Field(default_factory=FontsProfile)
    palette_hex: list[str] = Field(default_factory=list)
    palette_tolerance_delta_e: float = 3.0
    #: Colours seen once or twice and judged likely errors in the reference deck.
    #: Recorded so the user can see what was discarded, never enforced.
    palette_discarded: list[str] = Field(default_factory=list)
    logo: LogoProfile | None = None
    footer: FooterProfile = Field(default_factory=FooterProfile)
    title_geometry_tolerance_pt: float = 2.0


class GridProfile(_Model):
    """Learned alignment lines, in points from the top-left of the canvas."""

    columns_pt: list[float] = Field(default_factory=list)
    rows_pt: list[float] = Field(default_factory=list)
    tolerance_pt: float = 2.0

    def nearest_column(self, value: float) -> float | None:
        if not self.columns_pt:
            return None
        return min(self.columns_pt, key=lambda c: abs(c - value))

    def nearest_row(self, value: float) -> float | None:
        if not self.rows_pt:
            return None
        return min(self.rows_pt, key=lambda r: abs(r - value))

    def on_column(self, value: float) -> bool:
        nearest = self.nearest_column(value)
        return nearest is not None and abs(nearest - value) <= self.tolerance_pt

    def on_row(self, value: float) -> bool:
        nearest = self.nearest_row(value)
        return nearest is not None and abs(nearest - value) <= self.tolerance_pt


class NearMissWindow(_Model):
    """The window in which an alignment difference is a defect rather than a
    deliberate offset.

    Below ``min`` is indistinguishable from aligned; above ``max`` is plainly a
    different position and not a mistake.
    """

    min: float = 0.5
    max: float = 4.0

    def contains(self, delta: float) -> bool:
        return self.min <= abs(delta) <= self.max


class RecurringElement(_Model):
    """A shape that appears across slides at a consistent position."""

    key: str
    box_pt: Box
    archetypes: list[str] = Field(default_factory=list)
    support: int = 0


class LayoutProfile(_Model):
    safe_margin_pt: dict[str, Margins] = Field(default_factory=dict)
    grid: GridProfile = Field(default_factory=GridProfile)
    near_miss_alignment_pt: NearMissWindow = Field(default_factory=NearMissWindow)
    recurring: list[RecurringElement] = Field(default_factory=list)
    #: LO-004 fires above this share of the smaller shape's area.
    overlap_area_share: float = 0.05
    #: LO-008 fires when gutters in a detected row vary by more than this.
    gutter_stdev_pt: float = 1.0
    position_tolerance_pt: float = 2.0


class NumberFormat(_Model):
    """The numeric convention for one table column."""

    decimals: int | None = None
    thousands_separator: str | None = None
    negative_style: str | None = None


class TypographyProfile(_Model):
    quotes: Literal["curly", "straight"] | None = None
    title_case: Literal["sentence", "title", "upper"] | None = None
    bullet_terminal_punctuation: Literal["none", "period", "semicolon"] | None = None
    thousands_separator: str | None = None
    negative_style: Literal["parentheses", "minus"] | None = None
    decimal_places_by_column: Literal["consistent_within_column"] | None = None
    date_format: str | None = None
    currency_pattern: str | None = None
    unit_pattern: str | None = None
    #: Canonical surface form -> the variants that must be rewritten to it.
    canon_terms: dict[str, list[str]] = Field(default_factory=dict)


class HygieneProfile(_Model):
    allow_speaker_notes: bool = False
    allow_hidden_slides: bool = False
    allow_document_metadata: bool = False
    allow_comments: bool = False
    allow_external_relationships: bool = False
    placeholder_markers: list[str] = Field(
        default_factory=lambda: [
            "TODO",
            "TBD",
            "TK",
            "XXX",
            "lorem ipsum",
            "[ ]",
            "FIXME",
            "PLACEHOLDER",
            "INSERT",
            "??",
        ]
    )
    min_image_dpi: float = 150.0
    #: Additional typefaces the client considers standard on their estate.
    extra_standard_fonts: list[str] = Field(default_factory=list)
    #: Words the client uses that a general dictionary rejects.
    dictionary: list[str] = Field(default_factory=list)


class RulesProfile(_Model):
    disabled: list[str] = Field(default_factory=list)
    #: Rules to run even though they are disabled by default. LO-006 and TY-009
    #: are off out of the box -- one is approximate, the other needs a client
    #: dictionary to be useful -- and this is how a client who wants them opts in.
    enabled: list[str] = Field(default_factory=list)
    severity_overrides: dict[str, Severity] = Field(default_factory=dict)


class NotLearned(_Model):
    """A rule the engine declined to derive, and why.

    Populating this is not an admission of failure. Silence is correct when the
    evidence is thin, and a recorded reason is what stops a user assuming the
    check exists.
    """

    key: str
    reason: str


class DeferredQuestion(_Model):
    """A question the interview would ask, carried in the profile when deferred."""

    id: str
    field_path: str
    question: str
    options: list[str] = Field(default_factory=list)
    default: str = ""
    #: Estimated number of future findings that depend on the answer. Drives
    #: ranking, and the hard cap of twelve.
    impact: int = 0
    answered: bool = False
    answer: str | None = None


class Suppression(_Model):
    """One accepted finding, from ``tieout check --accept``."""

    rule_id: str
    slide_index: int | None = None
    shape_name: str | None = None
    note: str = ""
    count: int = 1


class SuppressionFile(_Model):
    client: str = ""
    suppressions: list[Suppression] = Field(default_factory=list)

    def matches(self, rule_id: str, slide_index: int | None) -> bool:
        for entry in self.suppressions:
            if entry.rule_id != rule_id:
                continue
            if entry.slide_index is None or entry.slide_index == slide_index:
                return True
        return False

    def repeat_offenders(self, threshold: int = 3) -> list[Suppression]:
        """Suppressions used enough times to suggest the rule is miscalibrated."""
        return [s for s in self.suppressions if s.count >= threshold]


class Profile(_Model):
    """A complete learned profile."""

    client: str
    version: int = 1
    generated_at: str = ""
    #: Reference deck filenames and slide counts the profile was learned from.
    sources: list[str] = Field(default_factory=list)

    slide: SlideProfile
    archetypes: dict[str, list[int]] = Field(default_factory=dict)
    brand: BrandProfile = Field(default_factory=BrandProfile)
    layout: LayoutProfile = Field(default_factory=LayoutProfile)
    typography: TypographyProfile = Field(default_factory=TypographyProfile)
    hygiene: HygieneProfile = Field(default_factory=HygieneProfile)
    rules: RulesProfile = Field(default_factory=RulesProfile)

    not_learned: list[NotLearned] = Field(default_factory=list)
    questions: list[DeferredQuestion] = Field(default_factory=list)
    #: Dotted field paths the user has edited, protected from re-learning.
    locks: list[str] = Field(default_factory=list)
    #: Dotted field path -> the evidence that produced the value. Rules read this
    #: to populate a finding's ``expected_provenance``.
    provenance: dict[str, str] = Field(default_factory=dict)
    #: Dotted field path -> the confidence of the derivation.
    confidence: dict[str, Confidence] = Field(default_factory=dict)

    # -- accessors ---------------------------------------------------------------

    def provenance_for(self, field_path: str) -> str | None:
        """The provenance string for a field, falling back to its ancestors.

        Falling back matters: a rule reporting on
        ``brand.logo.per_archetype.content`` should still be able to say where the
        logo evidence came from when only ``brand.logo`` carries a note.
        """
        if field_path in self.provenance:
            return self.provenance[field_path]
        parts = field_path.split(".")
        while len(parts) > 1:
            parts.pop()
            candidate = ".".join(parts)
            if candidate in self.provenance:
                return self.provenance[candidate]
        return None

    def confidence_for(self, field_path: str, default: Confidence = "high") -> Confidence:
        if field_path in self.confidence:
            return self.confidence[field_path]
        parts = field_path.split(".")
        while len(parts) > 1:
            parts.pop()
            candidate = ".".join(parts)
            if candidate in self.confidence:
                return self.confidence[candidate]
        return default

    def is_locked(self, field_path: str) -> bool:
        """Whether a field, or any ancestor of it, is locked."""
        return any(
            field_path == lock or field_path.startswith(f"{lock}.")
            for lock in self.locks
        )

    def archetype_of(self, slide_index: int) -> str | None:
        for name, indices in self.archetypes.items():
            if slide_index in indices:
                return name
        return None

    def rule_enabled(self, rule_id: str, *, default: bool) -> bool:
        """Whether a rule should run.

        An explicit entry in ``disabled`` always wins, so a client can turn off a
        rule they disagree with. Otherwise ``enabled`` can switch on a rule that
        ships off by default.
        """
        if rule_id in self.rules.disabled:
            return False
        if rule_id in self.rules.enabled:
            return True
        return default

    def severity_of(self, rule_id: str, default: Severity) -> Severity:
        return self.rules.severity_overrides.get(rule_id, default)

    def not_learned_keys(self) -> frozenset[str]:
        return frozenset(entry.key for entry in self.not_learned)

    def set_provenance(
        self, field_path: str, text: str, confidence: Confidence = "high"
    ) -> None:
        self.provenance[field_path] = text
        self.confidence[field_path] = confidence

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=False)
