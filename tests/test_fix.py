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
from tieout.rules.base import clear_caches, run_rules
from tieout_fix import apply_fix, move_fix, plan_fixes


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
