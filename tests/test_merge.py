"""Incremental learning.

Section 8.6's third constraint is the one with teeth: a merge must never silently
narrow a rule in a way that would newly fail a previously passing deck.
Narrowing is allowed; doing it silently is not. So most of these tests are about
whether the diff tells the truth, not just whether the value changed.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest

from tieout.learn.merge import LOCKED, NARROWED, WIDENED, merge
from tieout.profile.schema import (
    BoilerplateEntry,
    Box,
    BrandProfile,
    FontRole,
    FontsProfile,
    GridProfile,
    LayoutProfile,
    LogoProfile,
    Margins,
    NotLearned,
    PageNumberProfile,
    Profile,
    SlideProfile,
    TypographyProfile,
)


def _profile(**overrides: Any) -> Profile:
    base: dict[str, Any] = {
        "client": "acme",
        "slide": SlideProfile(width_pt=960.0, height_pt=540.0),
        "brand": BrandProfile(
            fonts=FontsProfile(
                allowed=["Gill Sans MT"],
                roles={"body": FontRole(min_pt=10.0, max_pt=14.0)},
            ),
            palette_hex=["#000000", "#FFFFFF", "#1F3864"],
            palette_tolerance_delta_e=6.0,
        ),
        "layout": LayoutProfile(
            safe_margin_pt={"content": Margins(top=36, right=36, bottom=48, left=36)},
            grid=GridProfile(columns_pt=[36.0, 924.0], rows_pt=[36.0]),
        ),
        "typography": TypographyProfile(quotes="curly", date_format="%d-%B-%Y"),
    }
    base.update(overrides)
    return Profile(**base)


def _changes(result, path):
    return [c for c in result.changes if c.path == path]


# --------------------------------------------------------------------------------------
# Widening
# --------------------------------------------------------------------------------------


def test_a_new_typeface_widens_the_approved_set():
    existing = _profile()
    incoming = _profile()
    incoming.brand.fonts.allowed = ["Gill Sans MT", "Arial"]

    result = merge(existing, incoming)
    assert set(result.profile.brand.fonts.allowed) == {"Gill Sans MT", "Arial"}
    change = _changes(result, "brand.fonts.allowed")[0]
    assert change.kind == WIDENED
    assert "Arial" in change.reason


def test_a_font_set_never_shrinks_on_merge():
    """A band that contracted because the second deck happened not to use the
    extremes would fail the first deck, which is the silent narrowing section 8.6
    forbids."""
    existing = _profile()
    incoming = _profile()
    incoming.brand.fonts.roles = {"body": FontRole(min_pt=11.0, max_pt=12.0)}

    result = merge(existing, incoming)
    body = result.profile.brand.fonts.roles["body"]
    assert body.min_pt == 10.0
    assert body.max_pt == 14.0
    assert not _changes(result, "brand.fonts.roles.body")


def test_a_wider_deck_widens_the_band():
    existing = _profile()
    incoming = _profile()
    incoming.brand.fonts.roles = {"body": FontRole(min_pt=9.0, max_pt=18.0)}

    result = merge(existing, incoming)
    body = result.profile.brand.fonts.roles["body"]
    assert (body.min_pt, body.max_pt) == (9.0, 18.0)
    assert _changes(result, "brand.fonts.roles.body")[0].kind == WIDENED


def test_two_exact_sets_merge_into_one_exact_set():
    existing = _profile()
    existing.brand.fonts.roles = {"title": FontRole(exact_pt=[20.0])}
    incoming = _profile()
    incoming.brand.fonts.roles = {"title": FontRole(exact_pt=[24.0])}

    result = merge(existing, incoming)
    assert result.profile.brand.fonts.roles["title"].exact_pt == [20.0, 24.0]


def test_a_role_seen_for_the_first_time_is_added():
    existing = _profile()
    incoming = _profile()
    incoming.brand.fonts.roles = {"footnote": FontRole(exact_pt=[7.0])}

    result = merge(existing, incoming)
    assert "footnote" in result.profile.brand.fonts.roles
    change = _changes(result, "brand.fonts.roles.footnote")[0]
    assert change.before == "not learned"


def test_a_genuinely_new_colour_joins_the_palette():
    existing = _profile()
    incoming = _profile()
    incoming.brand.palette_hex = ["#000000", "#C00000"]

    result = merge(existing, incoming)
    assert "#C00000" in result.profile.brand.palette_hex
    assert len(result.profile.brand.palette_hex) == 4


def test_a_near_identical_colour_does_not_duplicate_the_palette():
    """Comparison is perceptual, so #1F3865 is the same brand colour as #1F3864."""
    existing = _profile()
    incoming = _profile()
    incoming.brand.palette_hex = ["#1F3865"]

    result = merge(existing, incoming)
    assert result.profile.brand.palette_hex == existing.brand.palette_hex


def test_a_margin_loosens_to_the_tighter_of_the_two_decks():
    existing = _profile()
    incoming = _profile()
    incoming.layout.safe_margin_pt = {
        "content": Margins(top=24, right=36, bottom=48, left=36)
    }

    result = merge(existing, incoming)
    assert result.profile.layout.safe_margin_pt["content"].top == 24
    assert _changes(result, "layout.safe_margin_pt.content")[0].kind == WIDENED


def test_new_grid_lines_are_added():
    existing = _profile()
    incoming = _profile()
    incoming.layout.grid.columns_pt = [36.0, 480.0, 924.0]

    result = merge(existing, incoming)
    assert 480.0 in result.profile.layout.grid.columns_pt
    assert _changes(result, "layout.grid.columns_pt")[0].kind == WIDENED


def test_a_grid_line_within_tolerance_is_not_added_twice():
    existing = _profile()
    incoming = _profile()
    incoming.layout.grid.columns_pt = [37.0]

    result = merge(existing, incoming)
    assert result.profile.layout.grid.columns_pt == [36.0, 924.0]


# --------------------------------------------------------------------------------------
# The logo
# --------------------------------------------------------------------------------------


def _with_logo(
    box: Box | Literal["exempt"], archetype: str = "content"
) -> Profile:
    profile = _profile()
    profile.brand.logo = LogoProfile(
        image_sha1=["aaa"], per_archetype={archetype: box}
    )
    return profile


def test_a_logo_box_widens_its_tolerance_rather_than_moving():
    """Moving the expectation to a midpoint would put it where neither deck puts
    the logo, and the first deck's placement is the one already agreed."""
    existing = _with_logo(Box(left=852, top=24, width=72, height=24, tolerance_pt=2.0))
    incoming = _with_logo(Box(left=858, top=24, width=72, height=24, tolerance_pt=2.0))

    result = merge(existing, incoming)
    assert result.profile.brand.logo is not None
    box = result.profile.brand.logo.box_for("content")
    assert box is not None
    assert box.left == 852, "the expected position is unchanged"
    assert box.tolerance_pt >= 6.0, "the tolerance must now admit both placements"
    assert _changes(result, "brand.logo.per_archetype.content")[0].kind == WIDENED


def test_a_placement_inside_the_tolerance_changes_nothing():
    existing = _with_logo(Box(left=852, top=24, width=72, height=24, tolerance_pt=4.0))
    incoming = _with_logo(Box(left=854, top=24, width=72, height=24, tolerance_pt=2.0))
    assert not _changes(merge(existing, incoming), "brand.logo.per_archetype.content")


def test_presence_beats_an_earlier_exemption():
    """The first deck simply had no logo there; the second shows where it goes.
    Absence of evidence is not evidence of a rule."""
    existing = _with_logo("exempt", "agenda")
    incoming = _with_logo(
        Box(left=852, top=24, width=72, height=24, tolerance_pt=2.0), "agenda"
    )

    result = merge(existing, incoming)
    assert result.profile.brand.logo is not None
    assert isinstance(result.profile.brand.logo.box_for("agenda"), Box)
    change = _changes(result, "brand.logo.per_archetype.agenda")[0]
    assert change.kind == WIDENED
    assert "absence of evidence" in change.reason


def test_a_second_logo_image_is_admitted():
    existing = _with_logo(Box(left=852, top=24, width=72, height=24))
    incoming = _with_logo(Box(left=852, top=24, width=72, height=24))
    assert incoming.brand.logo is not None
    incoming.brand.logo.image_sha1 = ["bbb"]

    result = merge(existing, incoming)
    assert result.profile.brand.logo is not None
    assert set(result.profile.brand.logo.image_sha1) == {"aaa", "bbb"}


def test_a_first_logo_is_learned_when_the_profile_had_none():
    existing = _profile()
    incoming = _with_logo(Box(left=852, top=24, width=72, height=24))
    result = merge(existing, incoming)
    assert result.profile.brand.logo is not None
    assert _changes(result, "brand.logo")[0].before == "not learned"


# --------------------------------------------------------------------------------------
# Narrowing, which must always be explained
# --------------------------------------------------------------------------------------


def test_a_narrowing_change_states_what_would_newly_fail():
    """A merge that quietly tightened the palette and turned the client's last
    approved deck red would destroy more trust than the evidence was worth."""
    existing = _profile()
    incoming = _profile()
    incoming.brand.palette_hex = ["#1F3864", "#243D69"]

    result = merge(existing, incoming)
    narrowing = result.narrowing
    assert narrowing, "tightening the tolerance must be reported as a narrowing"
    for change in narrowing:
        assert change.consequence, f"{change.path} narrowed without saying what breaks"
    assert "stricter" in result.report()


def test_a_new_canon_term_is_reported_as_narrowing():
    existing = _profile()
    incoming = _profile()
    incoming.typography.canon_terms = {"Ashcombe Partners": ["Ashcombe partners"]}

    result = merge(existing, incoming)
    change = _changes(result, "typography.canon_terms.Ashcombe Partners")[0]
    assert change.kind == NARROWED
    assert "TY-005" in change.consequence


def test_dropping_a_requirement_is_a_widening_not_a_narrowing():
    """An archetype that carries a page number in one deck and not the other was
    never a house rule, so relaxing it loosens the profile."""
    existing = _profile()
    existing.brand.footer.page_number = PageNumberProfile(
        required_on=["content", "table_heavy"]
    )
    incoming = _profile()
    incoming.brand.footer.page_number = PageNumberProfile(required_on=["content"])

    result = merge(existing, incoming)
    assert result.profile.brand.footer.page_number is not None
    assert result.profile.brand.footer.page_number.required_on == ["content"]
    assert _changes(result, "brand.footer.page_number.required_on")[0].kind == WIDENED


def test_boilerplate_omitted_by_the_second_deck_stops_being_required_there():
    existing = _profile()
    existing.brand.footer.boilerplate = [
        BoilerplateEntry(text="Confidential", required_on=["content", "title"])
    ]
    incoming = _profile()
    incoming.brand.footer.boilerplate = [
        BoilerplateEntry(text="Confidential", required_on=["content"])
    ]

    result = merge(existing, incoming)
    assert result.profile.brand.footer.boilerplate[0].required_on == ["content"]


def test_a_string_the_second_deck_lacks_entirely_stays_required():
    """One deck omitting a footer is not evidence against the house rule; it may
    simply be a deck type that predates it."""
    existing = _profile()
    existing.brand.footer.boilerplate = [
        BoilerplateEntry(text="Confidential", required_on=["content"])
    ]
    incoming = _profile()

    result = merge(existing, incoming)
    assert result.profile.brand.footer.boilerplate[0].required_on == ["content"]


# --------------------------------------------------------------------------------------
# Conflicts and locks
# --------------------------------------------------------------------------------------


def test_decks_that_disagree_about_a_convention_retire_it():
    """A vote between two decks is a coin toss with extra steps. Keeping a rule
    that half the client's own material breaks is worse than having no rule."""
    existing = _profile()
    incoming = _profile()
    incoming.typography.quotes = "straight"

    result = merge(existing, incoming)
    assert result.profile.typography.quotes is None
    reasons = {entry.key: entry.reason for entry in result.profile.not_learned}
    assert "typography.quotes" in reasons
    assert "disagree" in reasons["typography.quotes"]


def test_a_convention_seen_for_the_first_time_is_simply_learned():
    existing = _profile()
    existing.typography.negative_style = None
    incoming = _profile()
    incoming.typography.negative_style = "parentheses"

    result = merge(existing, incoming)
    assert result.profile.typography.negative_style == "parentheses"


def test_a_locked_field_is_never_overwritten():
    """A lock is a decision a person made, and evidence does not outvote it."""
    existing = _with_logo(Box(left=852, top=24, width=72, height=24, tolerance_pt=2.0))
    existing.locks = ["brand.logo.per_archetype.content"]
    incoming = _with_logo(Box(left=600, top=99, width=72, height=24, tolerance_pt=2.0))

    result = merge(existing, incoming)
    assert result.profile.brand.logo is not None
    kept = result.profile.brand.logo.box_for("content")
    assert kept is not None
    assert kept.left == 852
    locked = _changes(result, "brand.logo.per_archetype.content")[0]
    assert locked.kind == LOCKED
    assert result.locked_out


def test_a_lock_on_an_ancestor_protects_its_children():
    existing = _profile()
    existing.locks = ["brand"]
    incoming = _profile()
    incoming.brand.fonts.allowed = ["Gill Sans MT", "Comic Sans MS"]

    result = merge(existing, incoming)
    assert result.profile.brand.fonts.allowed == ["Gill Sans MT"]


def test_a_different_canvas_size_is_a_conflict_not_an_average():
    """Two decks at different sizes are two house styles, and averaging them would
    produce a canvas neither uses."""
    existing = _profile()
    incoming = _profile(slide=SlideProfile(width_pt=720.0, height_pt=540.0))

    result = merge(existing, incoming)
    assert result.profile.slide.width_pt == 960.0
    assert "different canvas" in _changes(result, "slide")[0].reason


def test_archetype_assignments_are_not_unioned_across_decks():
    """Slide 4 of one deck and slide 4 of another are unrelated, so unioning the
    index lists would describe neither deck."""
    existing = _profile(archetypes={"content": [4, 5]})
    incoming = _profile(archetypes={"content": [9, 10]})

    result = merge(existing, incoming)
    assert result.profile.archetypes == {"content": [4, 5]}
    assert "not comparable across decks" in _changes(result, "archetypes")[0].reason


def test_merging_bumps_the_version_and_records_both_sources():
    existing = _profile(version=1, sources=["a.pptx (26 slides)"])
    incoming = _profile(sources=["b.pptx (18 slides)"])

    result = merge(existing, incoming)
    assert result.profile.version == 2
    assert result.profile.sources == ["a.pptx (26 slides)", "b.pptx (18 slides)"]


def test_a_key_that_is_now_learned_leaves_not_learned():
    existing = _profile(
        not_learned=[NotLearned(key="brand.fonts.roles.footnote", reason="thin")]
    )
    incoming = _profile()
    incoming.brand.fonts.roles = {"footnote": FontRole(exact_pt=[7.0])}

    result = merge(existing, incoming)
    assert "brand.fonts.roles.footnote" not in result.profile.not_learned_keys()


def test_merging_identical_profiles_changes_nothing_substantive():
    result = merge(_profile(), _profile())
    assert not result.narrowing
    substantive = [c for c in result.changes if c.kind in (WIDENED, NARROWED)]
    assert not substantive, [c.describe() for c in substantive]


def test_the_report_reads_as_prose():
    existing = _profile()
    incoming = _profile()
    incoming.brand.fonts.allowed = ["Gill Sans MT", "Arial"]
    report = merge(existing, incoming).report()
    assert "change(s) from the added deck" in report
    assert "brand.fonts.allowed" in report


def test_merging_a_profile_with_itself_widens_and_narrows_nothing():
    """Neutral notes are still emitted -- the archetype note always is -- so the
    property worth asserting is that no rule moved in either direction."""
    result = merge(_profile(), _profile())
    assert not result.narrowing
    assert not [c for c in result.changes if c.kind == WIDENED]


@pytest.mark.parametrize(
    "field", ["quotes", "title_case", "negative_style", "date_format"]
)
def test_every_scalar_convention_conflict_is_handled(field):
    existing = _profile()
    setattr(existing.typography, field, "curly" if field == "quotes" else "sentence"
            if field == "title_case" else "parentheses" if field == "negative_style"
            else "%d-%B-%Y")
    incoming = _profile()
    setattr(incoming.typography, field, "straight" if field == "quotes" else "title"
            if field == "title_case" else "minus" if field == "negative_style"
            else "%m/%d/%Y")

    result = merge(existing, incoming)
    assert getattr(result.profile.typography, field) is None
    assert f"typography.{field}" in result.profile.not_learned_keys()
