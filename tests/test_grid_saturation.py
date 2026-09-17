"""The learned grid has to stay selective, or LO-003 reports coincidence.

A real deck derived 53 row lines on a 540pt canvas. At near-miss width that put
more than half the vertical canvas inside the window of *some* row, so "this
edge just misses a grid line" was true of almost anywhere a shape could be put,
and every remaining LO-003 finding on that deck was on the y axis. 53 rows on
540pt is not a grid; it is a transcript of every y-coordinate the deck uses.

Two things are asserted here, and they pull in opposite directions on purpose.
The guard must silence an axis that has stopped carrying information, and it
must not silence a deck that genuinely has a grid to miss -- so every test that
drops an axis is paired with one that proves the rule still fires.
"""

from __future__ import annotations

import importlib

import pytest
from pptx import Presentation
from pptx.util import Emu, Pt

from tieout.cluster import coverage_share
from tieout.learn import learn_from_decks
from tieout.learn.merge import NEUTRAL, WIDENED, merge
from tieout.model.loader import load_deck
from tieout.profile.schema import GridProfile
from tieout.rules.base import run_rules

#: ``tieout.learn`` exports a ``derive_layout`` *function*, which shadows the
#: submodule of the same name on the package. Going through the module registry
#: gets the module, which is what carries the constants.
derive_layout_module = importlib.import_module("tieout.learn.derive_layout")

CANVAS_PT = (960.0, 540.0)
COLUMNS_PT = (60.0, 360.0, 660.0)
BOX_WIDTH_PT = 240.0
BOX_HEIGHT_PT = 18.0


def _body(tag: str) -> str:
    """Long enough that the slide reads as content, distinct enough that it is
    not boilerplate -- boilerplate is furniture, and furniture never reaches the
    grid."""
    return f"{tag} line of body copy long enough to read as content rather than chrome"


def _deck(path, tops_for_slide, slides=8, captions=False, drag=None):
    """Eight slides of boxes on three columns, at whatever tops are asked for.

    ``tops_for_slide(n)`` gives slide ``n`` its top edges. ``drag`` is
    ``(slide, column, offset)`` and nudges one box off its column, which is the
    defect LO-003 exists to catch.
    """
    presentation = Presentation()
    presentation.slide_width = Emu(int(CANVAS_PT[0] * 12700))
    presentation.slide_height = Emu(int(CANVAS_PT[1] * 12700))
    for n in range(slides):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        for row, top in enumerate(tops_for_slide(n)):
            for column, left in enumerate(COLUMNS_PT):
                if drag is not None and drag[:2] == (n, column) and row == 0:
                    left += drag[2]
                box = slide.shapes.add_textbox(
                    Pt(left), Pt(top), Pt(BOX_WIDTH_PT), Pt(BOX_HEIGHT_PT)
                )
                box.text_frame.text = _body(f"Cell {n}-{row}-{column}")
        if captions:
            # One caption per slide, each at a y nobody else uses and a couple of
            # points off a learned row. This is the shape that produced the real
            # deck's spurious findings: not misaligned, just somewhere no row was
            # ever meant to be.
            box = slide.shapes.add_textbox(
                Pt(COLUMNS_PT[0]), Pt(320.0 + n * 0.37), Pt(BOX_WIDTH_PT), Pt(14)
            )
            box.text_frame.text = _body(f"Caption {n}")
    presentation.save(str(path))
    return load_deck(path)


def _every_slide(tops):
    return lambda _n: tops


#: Twelve rows on every slide, 21pt apart. With their bottom edges that is 24
#: row lines on a 540pt canvas: every one of them recurs on every slide, so no
#: support threshold can thin them, and they blanket the axis.
SATURATING_TOPS = tuple(48.0 + k * 21.0 for k in range(12))

#: Three rows every slide shares, plus two more that only one pair of slides
#: uses. Together they saturate; the shared three alone do not.
STRUCTURAL_TOPS = (48.0, 96.0, 144.0)


def _mixed_tops(n: int) -> tuple[float, ...]:
    pair = n // 2
    return (*STRUCTURAL_TOPS, 210.0 + pair * 70.0, 242.0 + pair * 70.0)


@pytest.fixture
def unguarded(monkeypatch):
    """The behaviour before the saturation guard, for before/after comparison."""
    monkeypatch.setattr(derive_layout_module, "GRID_SATURATION_LIMIT", 1.0)


# --------------------------------------------------------------------------------------
# The measure itself
# --------------------------------------------------------------------------------------


def test_coverage_share_merges_overlapping_bands_rather_than_summing_them():
    """Two lines 2pt apart at 4pt half-width cover 10pt, not 16."""
    assert coverage_share([100.0, 102.0], 4.0, 1000.0) == pytest.approx(0.010)


def test_coverage_share_clips_bands_to_the_canvas():
    """A line on the edge has half its band off the slide, and off the slide is
    not somewhere a shape can be."""
    assert coverage_share([0.0], 4.0, 100.0) == pytest.approx(0.04)


def test_coverage_share_of_no_lines_is_nothing():
    assert coverage_share([], 4.0, 540.0) == 0.0
    assert coverage_share([100.0], 4.0, 0.0) == 0.0


def test_coverage_share_recognises_the_real_deck_s_row_axis():
    """53 rows on 540pt: the shape of the deck that prompted this guard, which
    measured 56% of its vertical canvas inside the near-miss window. Whatever
    else the measure does, it has to call that saturated."""
    lines = [10.0 * k for k in range(53)]
    assert coverage_share(lines, 4.0, 540.0) > 0.5


# --------------------------------------------------------------------------------------
# An axis that has stopped carrying information is dropped
# --------------------------------------------------------------------------------------


def test_a_saturated_row_axis_is_not_emitted(tmp_path):
    deck = _deck(tmp_path / "saturated.pptx", _every_slide(SATURATING_TOPS))
    profile = learn_from_decks([deck], "acme").profile

    assert profile.layout.grid.rows_pt == [], (
        "24 row lines on a 540pt canvas is a transcript of the deck's "
        "y-coordinates, not a grid, and a rule cannot be derived from it honestly"
    )


def test_dropping_the_row_axis_leaves_the_columns_alone(tmp_path):
    """The axis that is still selective keeps working. A guard that switched off
    the whole grid would cost more than the noise it removed."""
    deck = _deck(tmp_path / "saturated.pptx", _every_slide(SATURATING_TOPS))
    profile = learn_from_decks([deck], "acme").profile

    for expected in COLUMNS_PT:
        assert any(abs(expected - line) <= 2.0 for line in profile.layout.grid.columns_pt)


def test_a_dropped_axis_says_why_and_names_the_rule_that_stops(tmp_path):
    """Silence would be the one unacceptable outcome: the user would read an
    empty LO-003 as a clean deck rather than as a rule that never ran."""
    deck = _deck(tmp_path / "saturated.pptx", _every_slide(SATURATING_TOPS))
    profile = learn_from_decks([deck], "acme").profile

    reason = next(
        (entry.reason for entry in profile.not_learned if entry.key == "layout.grid.rows_pt"),
        None,
    )
    assert reason, "an axis that was dropped must say so in not_learned"
    assert "saturate" in reason
    assert "LO-003" in reason and "y axis" in reason


def test_the_guard_removes_findings_the_deck_did_not_earn(tmp_path, unguarded):
    """The invariant from the handoff, in miniature: check(D, learn(D)) must be
    empty on a deck with no defects in it.

    The captions are placed where no row was ever meant to be. Against a
    saturated row axis they land inside the near-miss window of a line they have
    nothing to do with, and get reported. That is precisely the finding the real
    deck kept producing.
    """
    deck = _deck(tmp_path / "noisy.pptx", _every_slide(SATURATING_TOPS), captions=True)

    before = learn_from_decks([deck], "acme").profile
    spurious = run_rules(deck, before, include=["LO-003"]).findings
    assert spurious, (
        "this deck has to reproduce the failure, or the test below proves nothing"
    )
    assert all(
        "row" in finding.message for finding in spurious
    ), "the reproduction should be on the row axis, as it was on the real deck"


def test_and_the_deck_it_learned_from_comes_back_clean(tmp_path):
    """The same deck, with the guard in place."""
    deck = _deck(tmp_path / "noisy.pptx", _every_slide(SATURATING_TOPS), captions=True)
    profile = learn_from_decks([deck], "acme").profile

    assert run_rules(deck, profile, include=["LO-003"]).findings == []


# --------------------------------------------------------------------------------------
# ...but the rule must still fire
# --------------------------------------------------------------------------------------


def test_a_shape_dragged_off_a_true_column_is_still_reported(tmp_path):
    """The guard the handoff asked for alongside this change. Drop the row axis
    by all means; a shape 3pt off a column the deck demonstrably uses is a real
    defect and has to survive it."""
    clean = _deck(tmp_path / "clean.pptx", _every_slide(SATURATING_TOPS))
    profile = learn_from_decks([clean], "acme").profile
    assert profile.layout.grid.rows_pt == []

    dragged = _deck(
        tmp_path / "dragged.pptx", _every_slide(SATURATING_TOPS), drag=(3, 1, 3.0)
    )
    findings = run_rules(dragged, profile, include=["LO-003"]).findings

    assert len(findings) == 1, [f.message for f in findings]
    assert "360pt column" in findings[0].message
    assert run_rules(clean, profile, include=["LO-003"]).findings == []


def test_a_sparse_row_grid_is_not_touched(tmp_path):
    """Three rows on a 540pt canvas is a grid. Nothing should be tightened, and
    the provenance should not claim anything was."""
    deck = _deck(tmp_path / "sparse.pptx", _every_slide(STRUCTURAL_TOPS))
    profile = learn_from_decks([deck], "acme").profile

    for expected in STRUCTURAL_TOPS:
        assert any(abs(expected - line) <= 2.0 for line in profile.layout.grid.rows_pt)
    provenance = profile.provenance_for("layout.grid.rows_pt") or ""
    assert "additionally required" not in provenance


# --------------------------------------------------------------------------------------
# The requirement is raised only as far as it has to be
# --------------------------------------------------------------------------------------


def test_a_row_the_whole_deck_uses_outlives_one_the_deck_barely_does(tmp_path):
    """The saturation is the trigger; the fix is the support threshold.

    Rather than a stricter constant for rows -- which would be a number fitted
    to whichever deck was on the desk -- the requirement to recur across slides
    is raised until the axis is selective again. Here that costs the rows two
    slides out of eight use and keeps the three every slide is built on.
    """
    deck = _deck(tmp_path / "mixed.pptx", _mixed_tops)
    profile = learn_from_decks([deck], "acme").profile

    rows = profile.layout.grid.rows_pt
    assert rows, "the deck has a real row grid underneath the noise"
    for expected in STRUCTURAL_TOPS:
        assert any(abs(expected - line) <= 2.0 for line in rows), (
            f"{expected}pt is used on every slide and is exactly what a row is"
        )
    for local in (210.0, 280.0, 350.0, 420.0):
        assert not any(abs(local - line) <= 2.0 for line in rows), (
            f"{local}pt is used by one pair of slides and is their layout, "
            f"not the deck's grid"
        )


def test_raising_the_requirement_is_recorded_in_the_provenance(tmp_path):
    deck = _deck(tmp_path / "mixed.pptx", _mixed_tops)
    profile = learn_from_decks([deck], "acme").profile

    provenance = profile.provenance_for("layout.grid.rows_pt") or ""
    assert "additionally required" in provenance
    assert "40%" in provenance
    assert profile.provenance_for("layout.grid.columns_pt")
    assert "additionally required" not in (
        profile.provenance_for("layout.grid.columns_pt") or ""
    ), "the column axis was never saturated and should not have been tightened"


# --------------------------------------------------------------------------------------
# Merging two decks
# --------------------------------------------------------------------------------------


def _profile_with_rows(rows):
    from tieout.profile.schema import LayoutProfile, Profile, SlideProfile

    return Profile(
        client="acme",
        slide=SlideProfile(width_pt=CANVAS_PT[0], height_pt=CANVAS_PT[1]),
        layout=LayoutProfile(grid=GridProfile(rows_pt=list(rows))),
    )


def test_merging_two_sparse_grids_still_widens():
    existing = _profile_with_rows([48.0, 96.0])
    incoming = _profile_with_rows([144.0, 192.0])

    result = merge(existing, incoming)

    assert result.profile.layout.grid.rows_pt == [48.0, 96.0, 144.0, 192.0]
    assert any(
        change.path == "layout.grid.rows_pt" and change.kind == WIDENED
        for change in result.changes
    )


def test_merging_declines_to_widen_a_grid_into_saturation():
    """Each deck's own grid comes in under the limit by construction, but a union
    of two need not. Two house styles with different but individually sparse rows
    make one axis that is neither, and the union is the only place this can come
    back."""
    existing = _profile_with_rows([40.0 + 20.0 * k for k in range(9)])
    incoming = _profile_with_rows([50.0 + 20.0 * k for k in range(9)])

    result = merge(existing, incoming)

    assert result.profile.layout.grid.rows_pt == existing.layout.grid.rows_pt
    change = next(
        change for change in result.changes if change.path == "layout.grid.rows_pt"
    )
    assert change.kind == NEUTRAL
    assert "re-learn from both together" in change.reason
