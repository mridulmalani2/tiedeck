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

import shutil
import zipfile

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Pt

from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.model.package import load_package
from tieout.rules.base import clear_caches, run_rules
from tieout_fix import apply_fix, plan_fixes


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
