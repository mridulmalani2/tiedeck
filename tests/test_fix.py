"""Applying corrections, and refusing to apply the rest.

Two things are worth testing here and the rest is plumbing.

The first is that a fix does what it says and nothing else: the deck that comes
back opens, keeps every slide and shape, and differs only where the finding
said it would. A fixer that quietly damages a package is worse than no fixer,
because the damage arrives inside a file someone is about to send.

The second is that the refusals hold. Geometry is never moved and no judgement
is ever guessed at, and those are the properties that make the feature safe to
put a button on.
"""

from __future__ import annotations

import re
import shutil
import zipfile

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Pt

from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.model.package import load_package
from tieout.model.units import EMU_PER_POINT, pt_to_emu
from tieout.profile.schema import Profile, SlideProfile
from tieout.rules.base import clear_caches, run_rules
from tieout_fix import apply_fix, move_fix, plan_fixes, resize_fix, retext_fix


def _audit(deck, profile):
    clear_caches()
    return run_rules(deck, profile)


def _deck(path, *, colour="1E2761", typeface="Calibri", notes=None, text=None):
    """A three-slide deck with a known fill, typeface and body text."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    for index in range(3):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        box = slide.shapes.add_textbox(Pt(60), Pt(80 + index * 10), Pt(600), Pt(40))
        box.name = f"Body {index}"
        frame = box.text_frame
        frame.text = text or f"Slide {index} body copy for the classifier to read."
        frame.paragraphs[0].runs[0].font.name = typeface
        shape = slide.shapes.add_shape(1, Pt(60), Pt(200), Pt(200), Pt(60))
        shape.name = f"Block {index}"
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(colour)
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    presentation.save(str(path))
    return load_deck(path)


@pytest.fixture
def house(tmp_path):
    """A profile learned from a clean deck, and that deck's path."""
    path = tmp_path / "reference.pptx"
    deck = _deck(path)
    return learn_from_decks([deck], "acme").profile


def _only_fix(deck, profile, rule_id):
    fixes = [f for f in plan_fixes(_audit(deck, profile), profile).values()
             if f.rule_id == rule_id]
    assert fixes, f"{rule_id} should have been fixable"
    return fixes[0]


# --------------------------------------------------------------------------- #
# The package survives
# --------------------------------------------------------------------------- #


def test_a_fix_changes_nothing_it_was_not_asked_to(tmp_path, house):
    before_path = tmp_path / "before.pptx"
    deck = _deck(before_path, notes="an internal aside")
    fix = _only_fix(deck, house, "HY-002")

    after_path = tmp_path / "after.pptx"
    assert apply_fix(before_path, after_path, fix).applied

    after = load_deck(after_path)
    assert after.slide_count == deck.slide_count
    assert sum(len(list(s.all_shapes())) for s in after.slides) == sum(
        len(list(s.all_shapes())) for s in deck.slides
    )
    with zipfile.ZipFile(after_path) as archive:
        assert archive.testzip() is None
    Presentation(str(after_path))  # it still opens


def test_a_fix_never_edits_the_file_it_was_given(tmp_path, house):
    """Undo is putting a path back, which only works while the earlier file is
    still the earlier file."""
    before_path = tmp_path / "before.pptx"
    deck = _deck(before_path, notes="an internal aside")
    original = before_path.read_bytes()

    apply_fix(before_path, tmp_path / "after.pptx", _only_fix(deck, house, "HY-002"))
    assert before_path.read_bytes() == original


def test_a_failed_fix_leaves_the_deck_as_it_was(tmp_path, house):
    from tieout_fix import Fix

    before_path = tmp_path / "before.pptx"
    _deck(before_path)
    after_path = tmp_path / "after.pptx"

    # A colour that appears nowhere: the applier must report that rather than
    # write a deck that differs from the one it was given.
    report = apply_fix(
        before_path,
        after_path,
        Fix(key="k", rule_id="BR-004", summary="", slides=(1,), kind="colour",
            payload={"from": "ABCDEF", "to": "123456"}),
    )
    assert not report.applied
    assert after_path.read_bytes() == before_path.read_bytes()


# --------------------------------------------------------------------------- #
# Each fix does its own job
# --------------------------------------------------------------------------- #


def test_the_metadata_fix_clears_what_hy004_reports(tmp_path, house):
    path = tmp_path / "meta.pptx"
    deck = _deck(path)
    presentation = Presentation(str(path))
    presentation.core_properties.author = "A Person"
    presentation.core_properties.last_modified_by = "A Person"
    presentation.save(str(path))
    deck = load_deck(path)

    out = tmp_path / "clean.pptx"
    assert apply_fix(path, out, _only_fix(deck, house, "HY-004")).applied
    assert load_package(out).core.identifying_fields() == {}


def test_the_notes_fix_empties_every_slide(tmp_path, house):
    path = tmp_path / "notes.pptx"
    deck = _deck(path, notes="do not send this")
    out = tmp_path / "quiet.pptx"

    assert apply_fix(path, out, _only_fix(deck, house, "HY-002")).applied
    assert all(not s.notes_text.strip() for s in load_deck(out).slides)


def test_the_colour_fix_recolours_to_the_palette(tmp_path, house):
    path = tmp_path / "gold.pptx"
    deck = _deck(path, colour="B08D3F")
    fix = _only_fix(deck, house, "BR-004")
    assert fix.payload["from"] == "B08D3F"

    out = tmp_path / "onbrand.pptx"
    assert apply_fix(path, out, fix).applied

    with zipfile.ZipFile(out) as archive:
        slides = b"".join(
            archive.read(n) for n in archive.namelist()
            if n.startswith("ppt/slides/slide")
        )
    assert b"B08D3F" not in slides
    assert fix.payload["to"].encode() in slides


def test_the_typeface_fix_needs_one_approved_face(tmp_path):
    """With three approved faces there is no way to know which one a run should
    have had, and picking one would be a guess dressed as a correction."""
    reference = tmp_path / "ref.pptx"
    profile = learn_from_decks([_deck(reference)], "acme").profile
    profile.brand.fonts.allowed = ["Calibri", "Cambria", "Georgia"]

    deck = _deck(tmp_path / "wrong.pptx", typeface="Comic Sans MS")
    fixable = {f.rule_id for f in plan_fixes(_audit(deck, profile), profile).values()}
    assert "BR-005" not in fixable

    profile.brand.fonts.allowed = ["Calibri"]
    fixable = {f.rule_id for f in plan_fixes(_audit(deck, profile), profile).values()}
    assert "BR-005" in fixable


def test_the_whitespace_fix_keeps_the_space_between_two_runs(tmp_path, house):
    """A run ending in a space is not trailing whitespace: it is the gap between
    two words of one sentence. Stripping it turned "Prepared for:  The Board"
    into "Prepared for:The Board" -- a correction that made the slide worse than
    the defect."""
    path = tmp_path / "spaced.pptx"
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = slide.shapes.add_textbox(Pt(60), Pt(80), Pt(600), Pt(40))
    paragraph = box.text_frame.paragraphs[0]
    paragraph.add_run().text = "Prepared for:  "
    paragraph.add_run().text = "The Board"
    presentation.save(str(path))

    from tieout_fix import Fix

    out = tmp_path / "tidy.pptx"
    assert apply_fix(
        path, out, Fix(key="k", rule_id="TY-002", summary="", slides=(1,),
                       kind="whitespace", payload={})
    ).applied

    text = Presentation(str(out)).slides[0].shapes[0].text_frame.text
    assert text == "Prepared for: The Board"


# --------------------------------------------------------------------------- #
# The refusals
# --------------------------------------------------------------------------- #


GEOMETRY_RULES = (
    "BR-001", "BR-002", "BR-003", "BR-008",
    "LO-001", "LO-002", "LO-003", "LO-004", "LO-005", "LO-006", "LO-007", "LO-008",
)

JUDGEMENT_RULES = ("CO-001", "CO-002", "CO-003", "HY-001", "HY-008", "TY-009")


@pytest.mark.parametrize("rule_id", GEOMETRY_RULES)
def test_no_fix_is_offered_for_geometry(rule_id):
    """Snapping the shapes LO-003 reported on a real deck to their nearest grid
    line drove one text box into its neighbour -- a major overlap where there had
    been none -- and turned a column spaced evenly to the point into one varying
    by five. Which alignment matters is a judgement about what the slide is for.
    """
    from tieout_fix import _BUILDERS

    assert rule_id not in _BUILDERS


@pytest.mark.parametrize("rule_id", JUDGEMENT_RULES)
def test_no_fix_is_offered_where_the_answer_is_unknowable(rule_id):
    """The tool can see that two figures disagree and cannot see which is right.
    A value invented here would be wrong invisibly, inside a file someone is
    about to send."""
    from tieout_fix import _BUILDERS

    assert rule_id not in _BUILDERS


def test_the_dirty_reference_deck_is_only_partly_fixable(dirty_deck, reference_profile):
    """The seeded deck carries every co-existing defect at once, so it is the
    broadest check that planning never offers a fix it cannot make."""
    fixes = plan_fixes(_audit(dirty_deck, reference_profile), reference_profile)
    assert fixes, "some of the seeded defects are mechanical"
    assert not {f.rule_id for f in fixes.values()} & set(GEOMETRY_RULES)
    assert not {f.rule_id for f in fixes.values()} & set(JUDGEMENT_RULES)


def test_every_fix_on_the_dirty_deck_applies_and_reduces_the_count(
    tmp_path, dirty_path, dirty_deck, reference_profile
):
    """End to end on the seeded deck: apply every fix offered, and assert the
    audit gets strictly better and the package stays readable."""
    current = tmp_path / "step0.pptx"
    shutil.copyfile(dirty_path, current)
    before = len(_audit(dirty_deck, reference_profile).findings)

    for step in range(1, 12):
        deck = load_deck(current)
        fixes = plan_fixes(_audit(deck, reference_profile), reference_profile)
        if not fixes:
            break
        fix = next(iter(fixes.values()))
        nxt = tmp_path / f"step{step}.pptx"
        report = apply_fix(current, nxt, fix)
        assert report.applied, f"{fix.summary}: {report.detail}"
        current = nxt

    after_deck = load_deck(current)
    after = len(_audit(after_deck, reference_profile).findings)
    assert after < before
    assert after_deck.slide_count == dirty_deck.slide_count


# --------------------------------------------------------------------------- #
# Moving a shape: the one fix whose value comes from the person
# --------------------------------------------------------------------------- #


def _moveable_deck(path):
    """Two slides, each with a text box and a table.

    A table because it is a ``graphicFrame``, which keeps its transform in
    ``p:xfrm`` rather than ``a:xfrm`` -- a distinction that costs nothing to get
    right and reports every chart and table at the slide origin when it is got
    wrong.
    """
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    for index in range(2):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        box = slide.shapes.add_textbox(Pt(60), Pt(80), Pt(300), Pt(40))
        box.name = f"Body {index}"
        box.text_frame.text = f"Body copy on slide {index + 1}."
        table = slide.shapes.add_table(2, 2, Pt(100), Pt(200), Pt(300), Pt(80))
        table.name = f"Table {index}"
    presentation.save(str(path))
    return load_deck(path)


def _emu(points):
    """``pt_to_emu`` with the "not specified" case ruled out, since every shape
    these tests build has a real box."""
    value = pt_to_emu(points)
    assert value is not None
    return value


def _shape(deck, slide_index, name):
    slide = deck.slide(slide_index)
    assert slide is not None, f"no slide {slide_index}"
    for shape in slide.all_shapes():
        if shape.ref.name == name:
            return shape
    raise AssertionError(f"no shape named {name} on slide {slide_index}")


def _move(shape, slide_index, x_emu, y_emu):
    return move_fix(
        slide_index=slide_index,
        shape_id=shape.ref.shape_id,
        shape_name=shape.ref.name,
        x_emu=x_emu,
        y_emu=y_emu,
        cx_emu=_emu(shape.width_pt),
        cy_emu=_emu(shape.height_pt),
        current_x_emu=_emu(shape.left_pt),
        current_y_emu=_emu(shape.top_pt),
    )


def _offset(deck, slide_index, name):
    shape = _shape(deck, slide_index, name)
    return (_emu(shape.left_pt), _emu(shape.top_pt))


@pytest.mark.parametrize("name", ["Body 0", "Table 0"])
def test_a_move_writes_the_offset_it_was_given(tmp_path, name):
    """Exactly the offset, with nothing rounded toward anything. The whole
    licence for writing geometry at all is that the number came from the person
    rather than from the tool."""
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, name)

    target = tmp_path / "after.pptx"
    assert apply_fix(source, target, _move(shape, 1, 1_234_567, 7_654_321)).applied

    assert _offset(load_deck(target), 1, name) == (1_234_567, 7_654_321)


@pytest.mark.parametrize("name", ["Body 0", "Table 0"])
def test_ten_nudges_out_and_ten_back_land_on_the_original_offset(tmp_path, name):
    """The property the arrow keys rest on.

    A nudge is ``EMU_PER_POINT`` and the offset is an integer number of EMU, so
    twenty moves are integer addition and the shape returns to where it was --
    not to within a rounding error of it. Done in points this drifts, and a
    shape that never quite goes back is a shape whose owner stops trusting undo.
    """
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, name)
    start = (_emu(shape.left_pt), _emu(shape.top_pt))

    current, x, y = source, start[0], start[1]
    for step in range(20):
        direction = 1 if step < 10 else -1
        x += direction * EMU_PER_POINT
        y += direction * EMU_PER_POINT
        nxt = tmp_path / f"step{step}.pptx"
        report = apply_fix(current, nxt, _move(shape, 1, x, y))
        assert report.applied, report.detail
        current = nxt

    assert _offset(load_deck(current), 1, name) == start


def test_a_move_changes_nothing_but_the_offset(tmp_path):
    """Not the size, not the rotation, not the other shapes, not the other
    slides, and not the package's ability to open."""
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, "Body 0")

    target = tmp_path / "after.pptx"
    assert apply_fix(source, target, _move(shape, 1, 90 * 12700, 300 * 12700)).applied

    after = load_deck(target)
    moved = _shape(after, 1, "Body 0")
    assert (moved.left_pt, moved.top_pt) == (90.0, 300.0)
    assert (moved.width_pt, moved.height_pt) == (shape.width_pt, shape.height_pt)
    assert moved.rotation == shape.rotation

    assert _offset(after, 1, "Table 0") == _offset(deck, 1, "Table 0")
    assert _offset(after, 2, "Body 1") == _offset(deck, 2, "Body 1")
    assert after.slide_count == deck.slide_count
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
    Presentation(str(target))


def test_a_move_gives_an_inherited_placeholder_a_transform_of_its_own(tmp_path):
    """A placeholder nobody has dragged has no ``a:xfrm`` at all -- the normal
    state of a title on a house template. Writing an offset there means writing
    the whole transform, and a transform with an offset and no extent is not
    valid OOXML, so the size the shape already resolves to is written with it.
    """
    source = tmp_path / "before.pptx"
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Inherited"
    presentation.save(str(source))

    deck = load_deck(source)
    slide = deck.slide(1)
    assert slide is not None
    title = next(s for s in slide.all_shapes() if s.is_placeholder)
    assert (title.width_pt, title.height_pt) != (0.0, 0.0), "it inherits a real box"

    target = tmp_path / "after.pptx"
    assert apply_fix(source, target, _move(title, 1, 120 * 12700, 40 * 12700)).applied

    after_slide = load_deck(target).slide(1)
    assert after_slide is not None
    after = next(s for s in after_slide.all_shapes() if s.is_placeholder)
    assert (after.left_pt, after.top_pt) == (120.0, 40.0)
    assert (after.width_pt, after.height_pt) == (title.width_pt, title.height_pt)


def test_a_move_follows_the_deck_order_not_the_part_numbers(tmp_path):
    """Slide parts are numbered in creation order, not presentation order. A
    deck whose slides have been reordered numbers them differently from how it
    reads, and a move applied to the wrong slide is this feature's worst failure
    because it is a silent one."""
    source = tmp_path / "before.pptx"
    _moveable_deck(source)

    # Reverse the presentation order without touching the parts themselves,
    # which is what dragging a slide in the rail does to the package.
    with zipfile.ZipFile(source) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}
    presentation = items["ppt/presentation.xml"].decode("utf-8")
    ids = re.findall(r"<p:sldId [^>]*/>", presentation)
    assert len(ids) == 2
    items["ppt/presentation.xml"] = presentation.replace(
        "".join(ids), "".join(reversed(ids))
    ).encode("utf-8")
    reordered = tmp_path / "reordered.pptx"
    with zipfile.ZipFile(reordered, "w", zipfile.ZIP_DEFLATED) as out:
        for name, payload in items.items():
            out.writestr(name, payload)

    deck = load_deck(reordered)
    # Slide 1 now holds what was built second.
    first = _shape(deck, 1, "Body 1")

    target = tmp_path / "after.pptx"
    assert apply_fix(reordered, target, _move(first, 1, 200 * 12700, 200 * 12700)).applied

    after = load_deck(target)
    assert _offset(after, 1, "Body 1") == (200 * 12700, 200 * 12700)
    assert _offset(after, 2, "Body 0") == _offset(deck, 2, "Body 0")


def test_a_move_naming_a_shape_that_is_not_there_writes_nothing(tmp_path):
    """The deck has changed under a page still showing the previous audit. The
    file it was given has to come back untouched, because a half-applied move is
    a deck someone sends."""
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, "Body 0")
    ghost = move_fix(
        slide_index=1,
        shape_id=999_999,
        shape_name="gone",
        x_emu=10,
        y_emu=10,
        cx_emu=_emu(shape.width_pt),
        cy_emu=_emu(shape.height_pt),
        current_x_emu=_emu(shape.left_pt),
        current_y_emu=_emu(shape.top_pt),
    )

    target = tmp_path / "after.pptx"
    report = apply_fix(source, target, ghost)
    assert not report.applied
    assert "999999" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_a_move_off_the_end_of_the_deck_writes_nothing(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, "Body 0")

    target = tmp_path / "after.pptx"
    report = apply_fix(source, target, _move(shape, 9, 10, 10))
    assert not report.applied
    assert "slide 9" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_planning_can_never_produce_a_move(dirty_deck, reference_profile):
    """The refusal above and this feature are the same rule, not two.

    A move is applied from coordinates a person supplied; it is never something
    the audit decided on. If one could arrive out of ``plan_fixes`` then TieOut
    would be choosing where shapes go after all, which is the thing that broke a
    real deck.
    """
    from tieout_fix import _BUILDERS, MOVE_KIND, MOVE_RULE_ID

    planned = plan_fixes(_audit(dirty_deck, reference_profile), reference_profile)
    assert all(fix.kind != MOVE_KIND for fix in planned.values())
    assert all(fix.rule_id != MOVE_RULE_ID for fix in planned.values())
    assert MOVE_RULE_ID not in _BUILDERS
    assert not set(_BUILDERS) & set(GEOMETRY_RULES)


# --------------------------------------------------------------------------------------
# Resizing: the same exception as a move, mirrored
# --------------------------------------------------------------------------------------


def _resize(shape, slide_index, cx_emu, cy_emu):
    return resize_fix(
        slide_index=slide_index,
        shape_id=shape.ref.shape_id,
        shape_name=shape.ref.name,
        x_emu=_emu(shape.left_pt),
        y_emu=_emu(shape.top_pt),
        cx_emu=cx_emu,
        cy_emu=cy_emu,
        current_cx_emu=_emu(shape.width_pt),
        current_cy_emu=_emu(shape.height_pt),
    )


def _extent(deck, slide_index, name):
    shape = _shape(deck, slide_index, name)
    return (_emu(shape.width_pt), _emu(shape.height_pt))


@pytest.mark.parametrize("name", ["Body 0", "Table 0"])
def test_a_resize_writes_the_extent_it_was_given(tmp_path, name):
    """The mirror of the move test with the same name: exactly the size, with
    nothing rounded toward anything."""
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, name)

    target = tmp_path / "after.pptx"
    assert apply_fix(source, target, _resize(shape, 1, 2_345_678, 8_765_432)).applied

    assert _extent(load_deck(target), 1, name) == (2_345_678, 8_765_432)


def test_ten_resizes_out_and_ten_back_land_on_the_original_extent(tmp_path):
    """The property a resize handle rests on, mirroring the move nudge test:
    EMU_PER_POINT steps are integer addition, so the shape returns to exactly
    the size it started at."""
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, "Body 0")
    start = (_emu(shape.width_pt), _emu(shape.height_pt))

    current, cx, cy = source, start[0], start[1]
    for step in range(20):
        direction = 1 if step < 10 else -1
        cx += direction * EMU_PER_POINT
        cy += direction * EMU_PER_POINT
        nxt = tmp_path / f"step{step}.pptx"
        report = apply_fix(current, nxt, _resize(shape, 1, cx, cy))
        assert report.applied, report.detail
        current = nxt

    assert _extent(load_deck(current), 1, "Body 0") == start


def test_a_resize_changes_nothing_but_the_extent(tmp_path):
    """Not the position, not the rotation, not the other shapes, not the other
    slides, and not the package's ability to open."""
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, "Body 0")

    target = tmp_path / "after.pptx"
    assert apply_fix(source, target, _resize(shape, 1, 400 * 12700, 90 * 12700)).applied

    after = load_deck(target)
    resized = _shape(after, 1, "Body 0")
    assert (resized.width_pt, resized.height_pt) == (400.0, 90.0)
    assert (resized.left_pt, resized.top_pt) == (shape.left_pt, shape.top_pt)
    assert resized.rotation == shape.rotation

    assert _extent(after, 1, "Table 0") == _extent(deck, 1, "Table 0")
    assert _extent(after, 2, "Body 1") == _extent(deck, 2, "Body 1")
    assert after.slide_count == deck.slide_count
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
    Presentation(str(target))


def test_a_resize_gives_an_inherited_placeholder_a_transform_of_its_own(tmp_path):
    """The mirror of the equivalent move test: a placeholder with no ``a:xfrm``
    of its own gets a whole one written, holding its own current position
    alongside the new size -- an extent with no offset is exactly as invalid
    as the reverse."""
    source = tmp_path / "before.pptx"
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Inherited"
    presentation.save(str(source))

    deck = load_deck(source)
    slide = deck.slide(1)
    assert slide is not None
    title = next(s for s in slide.all_shapes() if s.is_placeholder)
    assert (title.width_pt, title.height_pt) != (0.0, 0.0), "it inherits a real box"

    target = tmp_path / "after.pptx"
    assert apply_fix(source, target, _resize(title, 1, 300 * 12700, 60 * 12700)).applied

    after_slide = load_deck(target).slide(1)
    assert after_slide is not None
    after = next(s for s in after_slide.all_shapes() if s.is_placeholder)
    assert (after.width_pt, after.height_pt) == (300.0, 60.0)
    assert (after.left_pt, after.top_pt) == (title.left_pt, title.top_pt)


def test_a_resize_naming_a_shape_that_is_not_there_writes_nothing(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, "Body 0")
    ghost = resize_fix(
        slide_index=1,
        shape_id=999_999,
        shape_name="gone",
        x_emu=_emu(shape.left_pt),
        y_emu=_emu(shape.top_pt),
        cx_emu=10,
        cy_emu=10,
        current_cx_emu=_emu(shape.width_pt),
        current_cy_emu=_emu(shape.height_pt),
    )

    target = tmp_path / "after.pptx"
    report = apply_fix(source, target, ghost)
    assert not report.applied
    assert "999999" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_a_resize_off_the_end_of_the_deck_writes_nothing(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _moveable_deck(source)
    shape = _shape(deck, 1, "Body 0")

    target = tmp_path / "after.pptx"
    report = apply_fix(source, target, _resize(shape, 9, 10, 10))
    assert not report.applied
    assert "slide 9" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_planning_can_never_produce_a_resize(dirty_deck, reference_profile):
    """The same guarantee as :func:`test_planning_can_never_produce_a_move`,
    for the other geometry writer: a resize comes from a handle a person
    dragged, never from the audit."""
    from tieout_fix import _BUILDERS, RESIZE_KIND, RESIZE_RULE_ID

    planned = plan_fixes(_audit(dirty_deck, reference_profile), reference_profile)
    assert all(fix.kind != RESIZE_KIND for fix in planned.values())
    assert all(fix.rule_id != RESIZE_RULE_ID for fix in planned.values())
    assert RESIZE_RULE_ID not in _BUILDERS


def test_move_and_resize_keys_do_not_collide(tmp_path):
    """A move and a resize on the same shape are two different corrections and
    must never be looked up as one another."""
    from tieout_fix import move_key, resize_key

    assert move_key(1, 5) != resize_key(1, 5)


# --------------------------------------------------------------------------------------
# Group write-back: a move or a resize converted into a grouped shape's own space
# --------------------------------------------------------------------------------------


def _nested_group_deck(path):
    """A shape two groups deep, each resized after the fact so neither level
    is 1:1 with the level above it -- a compounded, not merely doubled,
    scale. Chosen so a bug in composing the two levels together, rather than
    in either one alone, would still show up as a wrong answer."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    outer = slide.shapes.add_group_shape()
    outer.name = "Outer"
    inner_group = outer.shapes.add_group_shape()
    inner_group.name = "Inner"
    leaf = inner_group.shapes.add_textbox(Pt(100), Pt(100), Pt(50), Pt(20))
    leaf.name = "Leaf"
    # chOff/chExt freeze at the auto-fit box each level had just before its
    # own resize -- (100, 100, 50, 20)pt for the inner group, then whatever
    # the inner group's own resize left the outer group's auto-fit at.
    inner_group.left, inner_group.top, inner_group.width, inner_group.height = (
        Pt(100), Pt(100), Pt(100), Pt(40),
    )
    outer.left, outer.top, outer.width, outer.height = Pt(50), Pt(50), Pt(200), Pt(80)

    presentation.save(str(path))
    return load_deck(path)


def test_a_move_through_two_nested_groups_composes_both_scales(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _nested_group_deck(source)
    shape = _shape(deck, 1, "Leaf")
    assert shape.ref.group_path == ("Outer", "Inner")
    before = (_emu(shape.left_pt), _emu(shape.top_pt))

    target = (before[0] + 400 * 12700, before[1] - 80 * 12700)
    fix = _move(shape, 1, *target)

    dest = tmp_path / "after.pptx"
    report = apply_fix(source, dest, fix)
    assert report.applied, report.detail
    assert _offset(load_deck(dest), 1, "Leaf") == target


def test_a_resize_through_two_nested_groups_composes_both_scales(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _nested_group_deck(source)
    shape = _shape(deck, 1, "Leaf")
    before = (_emu(shape.width_pt), _emu(shape.height_pt))

    target = (before[0] + 800 * 12700, before[1] + 200 * 12700)
    fix = _resize(shape, 1, *target)

    dest = tmp_path / "after.pptx"
    report = apply_fix(source, dest, fix)
    assert report.applied, report.detail
    assert _extent(load_deck(dest), 1, "Leaf") == target


def test_group_geometry_refusal_is_empty_for_a_plain_group(tmp_path):
    from tieout_fix import group_geometry_refusal

    deck = _nested_group_deck(tmp_path / "nested.pptx")
    shape = _shape(deck, 1, "Leaf")
    assert group_geometry_refusal(shape.raw_element) == ""


def test_group_geometry_refusal_names_a_rotated_ancestor(tmp_path):
    from tieout_fix import group_geometry_refusal

    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    group.name = "Group"
    leaf = group.shapes.add_textbox(Pt(100), Pt(100), Pt(50), Pt(20))
    leaf.name = "Leaf"
    group.rotation = 15.0
    path = tmp_path / "rotated.pptx"
    presentation.save(str(path))

    deck = load_deck(path)
    shape = _shape(deck, 1, "Leaf")
    refusal = group_geometry_refusal(shape.raw_element)
    assert "rotat" in refusal


# --------------------------------------------------------------------------------------
# Editing text: addressed by position, never by pattern
# --------------------------------------------------------------------------------------


def _text_deck(path):
    """One slide, one loose shape and one grouped shape, each with text that
    exercises the addressing this feature relies on: two runs in the first
    paragraph, and a second paragraph carrying a run, a line break, then
    another run."""
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    body = slide.shapes.add_textbox(Pt(60), Pt(80), Pt(400), Pt(100))
    body.name = "Body"
    frame = body.text_frame
    first = frame.paragraphs[0]
    first.add_run().text = "First "
    first.add_run().text = "sentence."
    second = frame.add_paragraph()
    second.add_run().text = "Before"
    second.add_line_break()
    second.add_run().text = "After"

    group = slide.shapes.add_group_shape()
    group.name = "Group"
    group.left, group.top, group.width, group.height = Pt(500), Pt(80), Pt(200), Pt(40)
    inside = group.shapes.add_textbox(Pt(500), Pt(80), Pt(200), Pt(40))
    inside.name = "Inside"
    inside.text_frame.paragraphs[0].add_run().text = "Grouped text."

    presentation.save(str(path))
    return load_deck(path)


def _retext(shape, slide_index, paragraph, run, text):
    return retext_fix(
        slide_index=slide_index,
        shape_id=shape.ref.shape_id,
        shape_name=shape.ref.name,
        paragraph=paragraph,
        run=run,
        text=text,
    )


def _text_of(deck, slide_index, name):
    shape = _shape(deck, slide_index, name)
    return [p.text for p in shape.text_frame_paragraphs]


def test_a_retext_replaces_exactly_the_addressed_run(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _text_deck(source)
    shape = _shape(deck, 1, "Body")
    assert [p.text for p in shape.text_frame_paragraphs] == [
        "First sentence.", "Before\nAfter",
    ]

    target = tmp_path / "after.pptx"
    fix = _retext(shape, 1, 0, 1, "revision.")
    report = apply_fix(source, target, fix)
    assert report.applied, report.detail

    after = load_deck(target)
    assert _text_of(after, 1, "Body") == ["First revision.", "Before\nAfter"]


def test_a_retext_touches_only_the_one_run(tmp_path):
    """Not the run before it in the same paragraph, not the other paragraph,
    not the font, not any other shape."""
    source = tmp_path / "before.pptx"
    deck = _text_deck(source)
    shape = _shape(deck, 1, "Body")
    before_font = shape.text_frame_paragraphs[0].runs[0].font

    target = tmp_path / "after.pptx"
    fix = _retext(shape, 1, 0, 1, "correction.")
    assert apply_fix(source, target, fix).applied

    after = load_deck(target)
    after_shape = _shape(after, 1, "Body")
    assert after_shape.text_frame_paragraphs[0].runs[0].text == "First "
    assert after_shape.text_frame_paragraphs[0].runs[0].font == before_font
    assert after_shape.text_frame_paragraphs[1].text == "Before\nAfter"


def test_a_retext_can_reach_a_shape_inside_a_group(tmp_path):
    """Unlike a move or a resize: a run's text carries no coordinate-space
    hazard, so there is nothing here for group membership to make unsafe."""
    source = tmp_path / "before.pptx"
    deck = _text_deck(source)
    shape = next(s for _, s in deck.all_shapes() if s.ref.name == "Inside")
    assert shape.ref.group_path, "the fixture puts this shape inside a group"

    target = tmp_path / "after.pptx"
    fix = _retext(shape, 1, 0, 0, "Edited inside a group.")
    report = apply_fix(source, target, fix)
    assert report.applied, report.detail

    after = load_deck(target)
    after_shape = next(s for _, s in after.all_shapes() if s.ref.name == "Inside")
    assert after_shape.text == "Edited inside a group."


def test_a_retext_refuses_a_line_break(tmp_path):
    """The run in between the two real runs of the second paragraph is a line
    break with no text of its own to replace."""
    source = tmp_path / "before.pptx"
    deck = _text_deck(source)
    shape = _shape(deck, 1, "Body")
    assert shape.text_frame_paragraphs[1].runs[1].text == "\n"

    target = tmp_path / "after.pptx"
    fix = _retext(shape, 1, 1, 1, "not a break any more")
    report = apply_fix(source, target, fix)
    assert not report.applied
    assert "line break" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_a_retext_naming_a_paragraph_that_is_not_there_is_refused(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _text_deck(source)
    shape = _shape(deck, 1, "Body")

    target = tmp_path / "after.pptx"
    fix = _retext(shape, 1, 5, 0, "nothing to attach this to")
    report = apply_fix(source, target, fix)
    assert not report.applied
    assert "paragraph 5" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_a_retext_naming_a_run_that_is_not_there_is_refused(tmp_path):
    source = tmp_path / "before.pptx"
    deck = _text_deck(source)
    shape = _shape(deck, 1, "Body")

    target = tmp_path / "after.pptx"
    fix = _retext(shape, 1, 0, 9, "nothing to attach this to")
    report = apply_fix(source, target, fix)
    assert not report.applied
    assert "run 9" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_a_retext_naming_a_shape_that_is_not_there_writes_nothing(tmp_path):
    source = tmp_path / "before.pptx"
    _text_deck(source)
    ghost = retext_fix(
        slide_index=1, shape_id=999_999, shape_name="gone",
        paragraph=0, run=0, text="anything",
    )

    target = tmp_path / "after.pptx"
    report = apply_fix(source, target, ghost)
    assert not report.applied
    assert "999999" in report.detail
    assert target.read_bytes() == source.read_bytes()


def test_a_retext_preserves_leading_or_trailing_whitespace(tmp_path):
    """PowerPoint collapses padding on an ``a:t`` with no ``xml:space``, so a
    person's deliberate leading or trailing space on an edit has to survive
    with the attribute that keeps it."""
    source = tmp_path / "before.pptx"
    deck = _text_deck(source)
    shape = _shape(deck, 1, "Body")

    target = tmp_path / "after.pptx"
    fix = _retext(shape, 1, 0, 0, "  Padded  ")
    assert apply_fix(source, target, fix).applied

    with zipfile.ZipFile(target) as archive:
        xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")
    assert '<a:t xml:space="preserve">  Padded  </a:t>' in xml


def test_planning_can_never_produce_a_retext(dirty_deck, reference_profile):
    from tieout_fix import _BUILDERS, RETEXT_KIND, RETEXT_RULE_ID

    planned = plan_fixes(_audit(dirty_deck, reference_profile), reference_profile)
    assert all(fix.kind != RETEXT_KIND for fix in planned.values())
    assert all(fix.rule_id != RETEXT_RULE_ID for fix in planned.values())
    assert RETEXT_RULE_ID not in _BUILDERS


def test_retext_keys_are_unique_per_run(tmp_path):
    from tieout_fix import retext_key

    assert retext_key(1, 5, 0, 0) != retext_key(1, 5, 0, 1)
    assert retext_key(1, 5, 0, 0) != retext_key(1, 5, 1, 0)


def test_the_core_never_imports_the_fixer():
    """The core promises not to write to a deck. A module inside it that could
    would make that promise a matter of reading the code rather than of its
    shape, which is the same reason ``tieout_review`` is its own package."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "tieout"
    offenders = []
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.startswith("tieout_fix") for name in names):
                offenders.append(str(source.relative_to(root)))
    assert not offenders, f"tieout must not import the fixer: {offenders}"


# --------------------------------------------------------------------------- #
# What a correction changed
#
# The three counts are the point, and "new" is the one worth testing hardest: a
# correction can legitimately expose a finding that was masked before, and a
# total that merely went down hides it.
# --------------------------------------------------------------------------- #


def _findings(deck, profile):
    return _audit(deck, profile).findings


def test_a_delta_against_nothing_is_everything_merely_present(dirty_deck, reference_profile):
    """The first check of a deck has changed nothing, so nothing is fixed and
    nothing is new."""
    from tieout_fix import delta

    found = _findings(dirty_deck, reference_profile)
    first = delta(None, found)
    assert (first.fixed, first.new) == (0, 0)
    assert first.remaining == len(found) == first.before == first.after


def test_an_unchanged_deck_reports_no_change(dirty_deck, reference_profile):
    """Identity has to survive a re-audit of the same file, or every correction
    would report the whole deck as fixed and re-arrived."""
    from tieout_fix import delta

    before = _findings(dirty_deck, reference_profile)
    after = _findings(dirty_deck, reference_profile)
    change = delta(before, after)
    assert (change.fixed, change.new) == (0, 0)
    assert change.remaining == len(before)


def test_the_three_counts_always_reconcile(tmp_path, dirty_path, dirty_deck, reference_profile):
    """``fixed + remaining`` is the total before and ``remaining + new`` the
    total after, at every step. A delta whose numbers do not add up is worse
    than no delta, because it is read as arithmetic."""
    from tieout_fix import delta

    current = tmp_path / "step0.pptx"
    shutil.copyfile(dirty_path, current)
    before = _findings(load_deck(current), reference_profile)

    for step in range(1, 8):
        deck = load_deck(current)
        fixes = plan_fixes(_audit(deck, reference_profile), reference_profile)
        if not fixes:
            break
        nxt = tmp_path / f"step{step}.pptx"
        assert apply_fix(current, nxt, next(iter(fixes.values()))).applied
        current = nxt
        after = _findings(load_deck(current), reference_profile)

        change = delta(before, after)
        assert change.fixed + change.remaining == len(before) == change.before
        assert change.remaining + change.new == len(after) == change.after
        before = after


def test_a_correction_that_exposes_a_finding_reports_it_as_new(tmp_path, house):
    """The case the whole feature exists for.

    Moving a shape onto a grid line can put it over its neighbour — which is
    exactly how automatic snapping damaged a real deck. The overlap was not
    reported before the move and is reported after it, and a delta that only
    said the total had changed would hide that from the person who would
    otherwise undo it.
    """
    from tieout_fix import delta

    source = tmp_path / "before.pptx"
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    for index, left in enumerate((60, 500)):
        box = slide.shapes.add_textbox(Pt(left), Pt(200), Pt(300), Pt(80))
        box.name = f"Column {index}"
        box.text_frame.text = f"Column {index} carries a sentence of body copy."
    presentation.save(str(source))

    deck = load_deck(source)
    before = _findings(deck, house)
    assert not [f for f in before if f.rule_id == "LO-004"], "they do not overlap yet"

    # Put the second box on top of the first, the way a person could.
    mover = _shape(deck, 1, "Column 1")
    target = tmp_path / "after.pptx"
    assert apply_fix(source, target, _move(mover, 1, 70 * 12700, 200 * 12700)).applied

    after = _findings(load_deck(target), house)
    change = delta(before, after)

    assert change.new >= 1, "the overlap is new"
    assert "LO-004" in {entry.rule_id for entry in change.arrived}
    assert change.worst_new is not None
    # And it reconciles, so the counts can be printed beside each other.
    assert change.remaining + change.new == len(after)


def test_the_sentence_names_new_findings_only_when_there_are_some():
    from tieout.model.deck import ShapeRef
    from tieout.rules.base import Finding
    from tieout_fix import delta

    def finding(rule_id, message):
        return Finding(
            rule_id=rule_id, category="layout", severity="major", confidence="high",
            where=ShapeRef(slide_index=1, shape_id=3, name="Box"),
            message=message, remedy=f"do something about {rule_id}",
        )

    a, b = finding("LO-002", "one"), finding("LO-004", "two")
    assert delta([a, b], [a]).sentence == "1 fixed, 1 remain"
    assert delta([a], [a, b]).sentence == "0 fixed, 1 remain, 1 new"


def test_two_findings_sharing_an_identity_are_counted_twice():
    """Counted rather than set-differenced, so the arithmetic holds even where a
    rule reports the same remedy twice about one shape."""
    from tieout.model.deck import ShapeRef
    from tieout.rules.base import Finding
    from tieout_fix import delta, finding_key

    def finding(message):
        return Finding(
            rule_id="LO-003", category="layout", severity="minor", confidence="high",
            where=ShapeRef(slide_index=4, shape_id=9, name="Panel"),
            message=message, remedy="Snap the edge to the grid line it is nearly on",
        )

    twice = [finding("left edge"), finding("right edge")]
    assert finding_key(twice[0]) == finding_key(twice[1]), "the fixture shares an identity"

    change = delta(twice, [twice[0]])
    assert (change.fixed, change.remaining, change.new) == (1, 1, 0)
    assert change.fixed + change.remaining == 2


# --------------------------------------------------------------------------------------
# One edit, offered once
# --------------------------------------------------------------------------------------


def test_two_rules_reaching_the_same_edit_are_planned_once() -> None:
    """A colour used by a shape and by a chart series is one rewrite.

    BR-004 and BR-011 both report it and both propose the identical recolour.
    Planned twice, the second application finds the colour already gone and
    reports that it changed nothing -- a failure notice for work that
    succeeded. On a real deck that is exactly what it did.
    """
    from tieout.rules.base import AuditResult, Finding
    from tieout_fix import plan_fixes

    def _finding(rule_id: str, slide: int, message: str) -> Finding:
        return Finding(
            rule_id=rule_id,
            category="brand",
            severity="major",
            confidence="high",
            where=slide,
            message=message,
            measured="#5C7EA3, Delta-E 15.6 from #6B7280",
            expected="a palette colour within Delta-E 2",
            remedy="Recolour #5C7EA3 to the palette's #6B7280",
        )

    result = AuditResult(
        deck_path="x.pptx", client="c", profile_version=1, generated_at=""
    )
    result.findings = [
        _finding("BR-004", 17, "#5C7EA3 is off the palette, used by 1 shape (fill)"),
        _finding("BR-011", 13, "#5C7EA3 is off the palette, used by 1 series"),
    ]

    profile = Profile(
        client="c", slide=SlideProfile(width_pt=960.0, height_pt=540.0)
    )
    profile.brand.palette_hex = ["#6B7280", "#0F2A4A"]
    profile.brand.palette_tolerance_delta_e = 2.0

    fixes = plan_fixes(result, profile)
    recolours = [fix for fix in fixes.values() if fix.kind == "colour"]
    assert len(recolours) == 1, [fix.summary for fix in recolours]
    assert set(recolours[0].slides) == {13, 17}
