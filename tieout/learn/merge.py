"""Incremental learning: folding a second reference deck into an existing profile.

``tieout learn --add DECK2.pptx --client acme``

Section 8.6 sets three constraints, and the third is the one that matters:

* A field listed in ``locks`` is never overwritten. A lock is a decision a person
  made, and evidence does not outvote it.
* Merging increases support, which can promote a dominant rule to invariant,
  widen an allowed set or tighten a tolerance.
* **It must never silently narrow a rule in a way that would newly fail a
  previously passing deck.** Narrowing is allowed; doing it silently is not.

That third constraint drives the whole design. Every change is classified as
widening, narrowing or neutral, and every narrowing change carries the sentence
explaining what would newly fail. The caller prints them. A merge that quietly
tightened the palette and turned the client's last approved deck red would
destroy more trust than the extra evidence was worth.

Where two decks genuinely disagree about a convention -- one uses curly quotes
throughout, the other straight -- the honest outcome is not a vote. It is to stop
claiming there is a convention, so the key moves to ``not_learned`` with both
observations recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from tieout.model.color import delta_e_76, parse_hex, try_parse_hex
from tieout.profile.schema import (
    BoilerplateEntry,
    Box,
    FontRole,
    Margins,
    NotLearned,
    Profile,
)

#: Colours within this Delta-E are the same brand colour and are not duplicated.
_PALETTE_DEDUPE_DELTA_E: Final[float] = 2.0

WIDENED: Final[str] = "widened"
NARROWED: Final[str] = "narrowed"
NEUTRAL: Final[str] = "neutral"
LOCKED: Final[str] = "locked"


@dataclass(frozen=True, slots=True)
class Change:
    """One difference the merge applied, or declined to apply."""

    path: str
    kind: str
    before: str
    after: str
    reason: str
    #: What would newly fail if this change is kept. Only set for narrowing.
    consequence: str = ""

    def describe(self) -> str:
        if self.kind == LOCKED:
            return f"{self.path}: unchanged, locked ({self.reason})"
        arrow = f"{self.before} -> {self.after}"
        line = f"{self.path}: {self.kind}, {arrow} ({self.reason})"
        if self.consequence:
            line += f"\n    consequence: {self.consequence}"
        return line


@dataclass
class MergeResult:
    """The merged profile and a full account of what changed."""

    profile: Profile
    changes: list[Change] = field(default_factory=list)

    @property
    def narrowing(self) -> list[Change]:
        return [c for c in self.changes if c.kind == NARROWED]

    @property
    def locked_out(self) -> list[Change]:
        return [c for c in self.changes if c.kind == LOCKED]

    def report(self) -> str:
        """The diff section 8.6 requires be printed after a merge."""
        if not self.changes:
            return "The added deck produced no changes: it agrees with the profile."
        lines = [f"{len(self.changes)} change(s) from the added deck:"]
        lines.extend(f"  {change.describe()}" for change in self.changes)
        if self.narrowing:
            lines.append("")
            lines.append(
                f"{len(self.narrowing)} change(s) made a rule stricter. A deck that "
                f"passed before may now fail; each consequence is listed above."
            )
        return "\n".join(lines)


def merge(existing: Profile, incoming: Profile) -> MergeResult:
    """Fold ``incoming`` into ``existing``, returning a new profile and a diff."""
    merged = existing.model_copy(deep=True)
    result = MergeResult(profile=merged)

    merged.sources = _union_strings(existing.sources, incoming.sources)
    merged.version = existing.version + 1

    _merge_slide(merged, existing, incoming, result)
    _merge_fonts(merged, existing, incoming, result)
    _merge_palette(merged, existing, incoming, result)
    _merge_logo(merged, existing, incoming, result)
    _merge_footer(merged, existing, incoming, result)
    _merge_layout(merged, existing, incoming, result)
    _merge_typography(merged, existing, incoming, result)
    _merge_archetypes(merged, incoming, result)
    _merge_not_learned(merged, existing, incoming)
    return result


def _locked(merged: Profile, path: str, result: MergeResult, reason: str) -> bool:
    if merged.is_locked(path):
        result.changes.append(
            Change(path=path, kind=LOCKED, before="", after="", reason=reason)
        )
        return True
    return False


def _record(
    result: MergeResult,
    path: str,
    kind: str,
    before: object,
    after: object,
    reason: str,
    consequence: str = "",
) -> None:
    result.changes.append(
        Change(
            path=path,
            kind=kind,
            before=_show(before),
            after=_show(after),
            reason=reason,
            consequence=consequence,
        )
    )


def _show(value: object) -> str:
    if isinstance(value, Box):
        return value.describe()
    if isinstance(value, Margins):
        return f"t{value.top:g} r{value.right:g} b{value.bottom:g} l{value.left:g}"
    if isinstance(value, FontRole):
        return value.describe()
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_show(item) for item in value) + "]"
    return str(value)


def _union_strings(first: list[str], second: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in [*first, *second]:
        seen.setdefault(value, None)
    return list(seen)


# --------------------------------------------------------------------------------------


def _merge_slide(
    merged: Profile, existing: Profile, incoming: Profile, result: MergeResult
) -> None:
    """Slide dimensions are invariant by construction, so a difference is a
    conflict rather than evidence: two decks at different sizes are two house
    styles, and averaging them would produce a size neither uses."""
    if (
        existing.slide.width_pt == incoming.slide.width_pt
        and existing.slide.height_pt == incoming.slide.height_pt
    ):
        return
    if _locked(merged, "slide", result, "slide dimensions"):
        return
    _record(
        result,
        "slide",
        NEUTRAL,
        f"{existing.slide.width_pt:g}x{existing.slide.height_pt:g}pt",
        f"{existing.slide.width_pt:g}x{existing.slide.height_pt:g}pt (kept)",
        f"the added deck is {incoming.slide.width_pt:g}x"
        f"{incoming.slide.height_pt:g}pt, a different canvas; the existing size "
        f"is kept and the added deck is not merged into this field",
    )


def _merge_fonts(
    merged: Profile, existing: Profile, incoming: Profile, result: MergeResult
) -> None:
    path = "brand.fonts.allowed"
    if not _locked(merged, path, result, "approved typefaces"):
        added = [
            name
            for name in incoming.brand.fonts.allowed
            if not existing.brand.fonts.permits_name(name)
        ]
        if added:
            merged.brand.fonts.allowed = _union_strings(
                existing.brand.fonts.allowed, incoming.brand.fonts.allowed
            )
            _record(
                result,
                path,
                WIDENED,
                existing.brand.fonts.allowed,
                merged.brand.fonts.allowed,
                f"the added deck uses {', '.join(added)}",
            )

    for role, incoming_role in incoming.brand.fonts.roles.items():
        role_path = f"brand.fonts.roles.{role}"
        if _locked(merged, role_path, result, f"{role} size band"):
            continue
        current = existing.brand.fonts.roles.get(role)
        if current is None:
            merged.brand.fonts.roles[role] = incoming_role.model_copy(deep=True)
            _record(
                result,
                role_path,
                WIDENED,
                "not learned",
                incoming_role,
                "the added deck provided the first evidence for this role",
            )
            continue
        widened = _widen_role(current, incoming_role)
        if widened is not None:
            merged.brand.fonts.roles[role] = widened
            _record(
                result,
                role_path,
                WIDENED,
                current,
                widened,
                "the added deck uses sizes the profile did not permit",
            )


def _widen_role(current: FontRole, incoming: FontRole) -> FontRole | None:
    """Widen a role to admit the added deck's sizes, or None if nothing changes.

    Only ever widens. A band that shrank because the second deck happened not to
    use the extremes would fail the first deck, which is exactly the silent
    narrowing section 8.6 forbids.

    The test is whether the current role already *permits* the incoming sizes,
    not whether the two endpoint sets overlap. A band stores its endpoints rather
    than its members, so comparing sets reports a 10-14pt band as needing to
    widen for an 11-12pt one and records a change that changes nothing.
    """
    current_values = _role_values(current)
    incoming_values = _role_values(incoming)
    if not incoming_values:
        return None
    if all(current.permits(value) for value in incoming_values):
        return None
    union = sorted(current_values | incoming_values)

    if current.exact_pt is not None and incoming.exact_pt is not None:
        return FontRole(exact_pt=union)
    return FontRole(min_pt=min(union), max_pt=max(union))


def _role_values(role: FontRole) -> set[float]:
    if role.exact_pt is not None:
        return set(role.exact_pt)
    values: set[float] = set()
    if role.min_pt is not None:
        values.add(role.min_pt)
    if role.max_pt is not None:
        values.add(role.max_pt)
    return values


def _merge_palette(
    merged: Profile, existing: Profile, incoming: Profile, result: MergeResult
) -> None:
    path = "brand.palette_hex"
    if _locked(merged, path, result, "palette"):
        return

    current = [parse_hex(value) for value in existing.brand.palette_hex]
    added: list[str] = []
    for value in incoming.brand.palette_hex:
        colour = try_parse_hex(value)
        if colour is None:
            continue
        if all(delta_e_76(colour, held) > _PALETTE_DEDUPE_DELTA_E for held in current):
            current.append(colour)
            added.append(colour.hex)

    if added:
        merged.brand.palette_hex = [colour.hex for colour in current]
        _record(
            result,
            path,
            WIDENED,
            existing.brand.palette_hex,
            merged.brand.palette_hex,
            f"the added deck uses {', '.join(added)}",
        )

    # A wider palette means colours sit closer together, so the tolerance that
    # could never merge two brand colours is recomputed rather than kept.
    tolerance_path = "brand.palette_tolerance_delta_e"
    if len(current) >= 2 and not merged.is_locked(tolerance_path):
        minimum = min(
            delta_e_76(a, b)
            for index, a in enumerate(current)
            for b in current[index + 1 :]
        )
        recomputed = max(2.0, min(round(minimum / 2.0, 2), 6.0))
        if recomputed < existing.brand.palette_tolerance_delta_e:
            merged.brand.palette_tolerance_delta_e = recomputed
            _record(
                result,
                tolerance_path,
                NARROWED,
                existing.brand.palette_tolerance_delta_e,
                recomputed,
                f"the widened palette brings two colours within "
                f"{minimum:.1f} Delta-E, so the tolerance must tighten to keep "
                f"them distinct",
                consequence=(
                    "a colour previously accepted as a near miss of a palette "
                    "entry may now be reported by BR-004"
                ),
            )


def _merge_logo(
    merged: Profile, existing: Profile, incoming: Profile, result: MergeResult
) -> None:
    if incoming.brand.logo is None:
        return
    if existing.brand.logo is None:
        if not _locked(merged, "brand.logo", result, "logo"):
            merged.brand.logo = incoming.brand.logo.model_copy(deep=True)
            _record(
                result,
                "brand.logo",
                WIDENED,
                "not learned",
                "learned",
                "the added deck provided the first logo evidence",
            )
        return

    hashes_path = "brand.logo.image_sha1"
    new_hashes = [
        value
        for value in incoming.brand.logo.image_sha1
        if value not in existing.brand.logo.image_sha1
    ]
    if new_hashes and not _locked(merged, hashes_path, result, "logo identity"):
        assert merged.brand.logo is not None
        merged.brand.logo.image_sha1 = _union_strings(
            existing.brand.logo.image_sha1, incoming.brand.logo.image_sha1
        )
        _record(
            result,
            hashes_path,
            WIDENED,
            f"{len(existing.brand.logo.image_sha1)} image(s)",
            f"{len(merged.brand.logo.image_sha1)} image(s)",
            "the added deck uses a logo image the profile did not know",
        )

    for archetype, entry in incoming.brand.logo.per_archetype.items():
        path = f"brand.logo.per_archetype.{archetype}"
        if _locked(merged, path, result, f"logo box on {archetype} slides"):
            continue
        assert merged.brand.logo is not None
        current = existing.brand.logo.per_archetype.get(archetype)

        if current is None:
            merged.brand.logo.per_archetype[archetype] = (
                entry if isinstance(entry, str) else entry.model_copy(deep=True)
            )
            _record(
                result, path, WIDENED, "not checked", entry,
                f"the added deck provided the first evidence for {archetype} slides",
            )
            continue

        if current == "exempt" and isinstance(entry, Box):
            # Presence is stronger evidence than absence: the first deck simply
            # had no logo there, the second shows where it goes.
            merged.brand.logo.per_archetype[archetype] = entry.model_copy(deep=True)
            _record(
                result, path, WIDENED, "exempt", entry,
                f"the added deck does place a logo on {archetype} slides, so the "
                f"exemption was an absence of evidence rather than a rule",
            )
            continue

        if isinstance(current, Box) and isinstance(entry, Box):
            widened = _widen_box(current, entry)
            if widened is not None:
                merged.brand.logo.per_archetype[archetype] = widened
                _record(
                    result, path, WIDENED, current, widened,
                    f"the added deck places the logo up to "
                    f"{_box_distance(current, entry):.1f}pt away, so the tolerance "
                    f"widens to accept both",
                )


def _widen_box(current: Box, incoming: Box) -> Box | None:
    """Widen a box's tolerance to cover the added deck's placement.

    The expected position is left alone. Moving it to a midpoint would place the
    expectation where neither deck puts the element, and the first deck's
    placement is the one already agreed with the client.
    """
    distance = _box_distance(current, incoming)
    if distance <= current.tolerance_pt:
        return None
    return current.model_copy(update={"tolerance_pt": round(distance + 0.5, 2)})


def _box_distance(a: Box, b: Box) -> float:
    return max(
        abs(a.left - b.left),
        abs(a.top - b.top),
        abs(a.width - b.width),
        abs(a.height - b.height),
    )


def _merge_footer(
    merged: Profile, existing: Profile, incoming: Profile, result: MergeResult
) -> None:
    current_pn = existing.brand.footer.page_number
    incoming_pn = incoming.brand.footer.page_number
    if current_pn is not None and incoming_pn is not None:
        path = "brand.footer.page_number.required_on"
        if not _locked(merged, path, result, "where page numbers are required"):
            kept = [a for a in current_pn.required_on if a in incoming_pn.required_on]
            dropped = [a for a in current_pn.required_on if a not in kept]
            if dropped:
                assert merged.brand.footer.page_number is not None
                merged.brand.footer.page_number.required_on = kept
                _record(
                    result, path, WIDENED, current_pn.required_on, kept,
                    f"the added deck has no page number on its {', '.join(dropped)} "
                    f"slides, so requiring one there was not the house rule",
                )

    for entry in existing.brand.footer.boilerplate:
        incoming_entry = next(
            (e for e in incoming.brand.footer.boilerplate if e.text == entry.text), None
        )
        path = f"brand.footer.boilerplate.{entry.text[:32]}"
        if incoming_entry is None:
            _record(
                result, path, NEUTRAL, entry.required_on, entry.required_on,
                "the added deck does not carry this string at all; the requirement "
                "is kept, since one deck omitting it is not evidence against it",
            )
            continue
        if _locked(merged, path, result, "required boilerplate"):
            continue
        kept = [a for a in entry.required_on if a in incoming_entry.required_on]
        if len(kept) != len(entry.required_on):
            dropped = [a for a in entry.required_on if a not in kept]
            _replace_boilerplate(merged, entry.text, kept)
            _record(
                result, path, WIDENED, entry.required_on, kept,
                f"the added deck omits it on its {', '.join(dropped)} slides",
            )


def _replace_boilerplate(merged: Profile, text: str, required_on: list[str]) -> None:
    merged.brand.footer.boilerplate = [
        BoilerplateEntry(
            text=item.text,
            required_on=required_on if item.text == text else item.required_on,
            is_confidentiality=item.is_confidentiality,
            match_normalised=item.match_normalised,
        )
        for item in merged.brand.footer.boilerplate
    ]


def _merge_layout(
    merged: Profile, existing: Profile, incoming: Profile, result: MergeResult
) -> None:
    for archetype, incoming_margins in incoming.layout.safe_margin_pt.items():
        path = f"layout.safe_margin_pt.{archetype}"
        if _locked(merged, path, result, f"{archetype} safe margin"):
            continue
        current = existing.layout.safe_margin_pt.get(archetype)
        if current is None:
            merged.layout.safe_margin_pt[archetype] = incoming_margins.model_copy()
            _record(
                result, path, WIDENED, "not checked", incoming_margins,
                f"the added deck provided the first {archetype} content to measure",
            )
            continue
        loosened = Margins(
            top=min(current.top, incoming_margins.top),
            right=min(current.right, incoming_margins.right),
            bottom=min(current.bottom, incoming_margins.bottom),
            left=min(current.left, incoming_margins.left),
        )
        if loosened != current:
            merged.layout.safe_margin_pt[archetype] = loosened
            _record(
                result, path, WIDENED, current, loosened,
                "the added deck places content closer to the edge, so the safe "
                "margin loosens to the tighter of the two decks",
            )

    path = "layout.grid"
    if not _locked(merged, path, result, "alignment grid"):
        for attribute in ("columns_pt", "rows_pt"):
            current_lines: list[float] = list(getattr(existing.layout.grid, attribute))
            incoming_lines: list[float] = list(getattr(incoming.layout.grid, attribute))
            tolerance = existing.layout.grid.tolerance_pt
            added = [
                line
                for line in incoming_lines
                if all(abs(line - held) > tolerance for held in current_lines)
            ]
            if added:
                setattr(
                    merged.layout.grid,
                    attribute,
                    sorted(current_lines + added),
                )
                _record(
                    result, f"{path}.{attribute}", WIDENED,
                    f"{len(current_lines)} line(s)",
                    f"{len(current_lines) + len(added)} line(s)",
                    "the added deck aligns to "
                    + ", ".join(f"{line:g}pt" for line in added[:6]),
                )


def _merge_typography(
    merged: Profile, existing: Profile, incoming: Profile, result: MergeResult
) -> None:
    """Where two decks disagree about a convention, stop claiming there is one.

    A vote between two decks is not evidence, it is a coin toss with extra steps.
    Retiring the key to ``not_learned`` is the outcome that does not put a rule in
    front of the user that half their own material breaks.
    """
    scalar_fields = (
        "quotes",
        "title_case",
        "bullet_terminal_punctuation",
        "thousands_separator",
        "negative_style",
        "decimal_places_by_column",
        "date_format",
        "currency_pattern",
        "unit_pattern",
    )
    for name in scalar_fields:
        path = f"typography.{name}"
        current = getattr(existing.typography, name)
        added = getattr(incoming.typography, name)
        if added is None or current == added:
            continue
        if _locked(merged, path, result, f"{name} convention"):
            continue
        if current is None:
            setattr(merged.typography, name, added)
            _record(
                result, path, WIDENED, "not learned", added,
                "the added deck provided the first evidence for this convention",
            )
            continue
        setattr(merged.typography, name, None)
        merged.not_learned.append(
            NotLearned(
                key=path,
                reason=(
                    f"the reference decks disagree: {current!r} in one and "
                    f"{added!r} in the other, so there is no convention to enforce"
                ),
            )
        )
        _record(
            result, path, WIDENED, current, "not learned",
            f"the added deck uses {added!r} where the profile expected {current!r}",
        )

    path = "typography.canon_terms"
    if not _locked(merged, path, result, "terminology canon"):
        for term, variants in incoming.typography.canon_terms.items():
            if term not in merged.typography.canon_terms:
                merged.typography.canon_terms[term] = list(variants)
                _record(
                    result, f"{path}.{term}", NARROWED, "not checked", term,
                    "the added deck establishes this term",
                    consequence=(
                        f"a deck spelling {term!r} differently will now be "
                        f"reported by TY-005"
                    ),
                )
            else:
                existing_variants = merged.typography.canon_terms[term]
                new_variants = [v for v in variants if v not in existing_variants]
                if new_variants:
                    existing_variants.extend(new_variants)
                    _record(
                        result, f"{path}.{term}", NARROWED,
                        f"{len(existing_variants) - len(new_variants)} variant(s)",
                        f"{len(existing_variants)} variant(s)",
                        f"the added deck contains {', '.join(new_variants)}",
                        consequence=(
                            "those spellings will now be reported by TY-005"
                        ),
                    )


def _merge_archetypes(
    merged: Profile, incoming: Profile, result: MergeResult
) -> None:
    """Archetype assignments are per deck and are not merged.

    Slide 4 of one deck and slide 4 of another are unrelated, so unioning the
    index lists would produce a classification that describes neither. The
    existing assignment is kept as a record of the first reference deck; the
    added deck is listed in ``sources``.
    """
    incoming_total = sum(len(v) for v in incoming.archetypes.values())
    if not incoming_total:
        return
    _record(
        result,
        "archetypes",
        NEUTRAL,
        f"{sum(len(v) for v in merged.archetypes.values())} slides",
        f"{sum(len(v) for v in merged.archetypes.values())} slides (kept)",
        f"the added deck's {incoming_total} slide classifications are not merged: "
        f"slide numbers are not comparable across decks",
    )


def _merge_not_learned(
    merged: Profile, existing: Profile, incoming: Profile
) -> None:
    """Drop a ``not_learned`` entry once the merged profile has learned it."""
    del existing, incoming
    learned_paths: list[NotLearned] = []
    for entry in merged.not_learned:
        if _path_now_populated(merged, entry.key):
            continue
        learned_paths.append(entry)
    seen: dict[str, NotLearned] = {}
    for entry in learned_paths:
        seen.setdefault(entry.key, entry)
    merged.not_learned = list(seen.values())


def _path_now_populated(profile: Profile, path: str) -> bool:
    current: object = profile
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return False
    return current not in ([], {}, "")
