"""Constructions a real deck contains, held to the tool's own acceptance criterion.

The suite's false-positive guard runs against the deck TieOut generates for
itself. That deck is built to fit TieOut's assumptions, which makes it the one
deck that cannot test them: pointed at a real twenty-slide banking deck, a
profile learned from that deck reported it 168 times, and all but a handful were
the tool disagreeing with itself.

Each construction below is one of those causes, reduced to the smallest deck that
contains it. They are built here rather than added to the shared fixture because
the shared fixture is asserted against by name and by count across the suite, and
because what is being tested is precisely the stuff TieOut's own generator would
never produce.

The assertion is the same every time, and it is the one that matters: learn from
the deck, check the same deck, and find nothing above ``info``. ``info`` is
allowed because a deliberate bleed is reported there by design -- visible, and
not a gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.presentation import Presentation as PresentationPart
from pptx.util import Emu, Pt

from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.rules.base import clear_caches

NAVY = RGBColor.from_string("0F2A4A")
GOLD = RGBColor.from_string("C9A227")
GREY = RGBColor.from_string("6B7280")


def _deck() -> PresentationPart:
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    return presentation


def _blank(presentation: PresentationPart):
    return presentation.slides.add_slide(presentation.slide_layouts[6])


def _text(slide, left, top, width, height, text, size=11, colour=NAVY, align=None):
    box = slide.shapes.add_textbox(Pt(left), Pt(top), Pt(width), Pt(height))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.name = "Calibri"
    run.font.color.rgb = colour
    if align is not None:
        paragraph.alignment = align
    return box


def _rect(slide, left, top, width, height, colour, alpha_pct=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Pt(left), Pt(top), Pt(width), Pt(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = colour
    shape.line.fill.background()
    if alpha_pct is not None:
        _set_alpha(shape, alpha_pct)
    return shape


def _set_alpha(shape, percent: int) -> None:
    """Put an ``a:alpha`` on the shape's solid fill, as a designer's tint does."""
    from lxml import etree

    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    clr = shape._element.spPr.find(f"{ns}solidFill/{ns}srgbClr")
    assert clr is not None
    alpha = etree.SubElement(clr, f"{ns}alpha")
    alpha.set("val", str(int(percent * 1000)))


def _chrome(slide, page: int) -> None:
    """The lockup and footer every slide carries, so they read as chrome.

    On the real deck the wordmark is furniture, excluded from what the profile
    learns and from what the layout rules measure. A wordmark appearing on one
    slide of five is not furniture to anybody, so putting it on every slide is
    what makes this fixture resemble the thing it stands in for.
    """
    _text(slide, 835, 27, 166, 22, "KESTREL PARTNERS", size=6, colour=NAVY)
    _text(slide, 36, 510, 500, 14, "Project Kestrel  |  Strictly Private", size=8, colour=GREY)
    _text(slide, 900, 510, 24, 14, str(page), size=8, colour=GREY)


def _build(path: Path) -> Path:
    """A five-slide deck in the shape of a real one, carrying six constructions.

    Shaped like a deck rather than like six test cases on purpose. The archetype
    classifier decides what a slide is from how much of what it carries, and the
    boilerplate match that turns the wordmark into chrome is keyed on the
    archetypes it recurs across -- so a slide too sparse to classify is a slide
    whose chrome is measured as content, and the fixture would then be testing
    the sparseness rather than the construction.
    """
    presentation = _deck()

    # --- 1. cover: a decorative graphic bled off the corner ---------------------
    cover = _blank(presentation)
    circle = cover.shapes.add_shape(MSO_SHAPE.OVAL, Pt(760), Pt(-100), Pt(374), Pt(374))
    circle.fill.solid()
    circle.fill.fore_color.rgb = NAVY
    circle.line.fill.background()
    _set_alpha(circle, 12)
    _text(cover, 36, 180, 600, 48, "Project Kestrel", size=38)
    _text(cover, 36, 240, 600, 24, "Confidential Investor Presentation", size=14)
    _text(cover, 36, 276, 600, 20, "Industrial analytics", size=11)
    _chrome(cover, 1)

    # --- 2. market overview: one brand colour at four opacities ------------------
    #     Flattening each against the page turned one colour into four that are
    #     on no palette, and BR-004 reported the house style as four breaches.
    market = _blank(presentation)
    _rect(market, 36, 30, 4, 24, GOLD)
    _text(market, 48, 36, 600, 24, "MARKET OVERVIEW", size=14)
    _text(market, 36, 66, 880, 22, "The addressable market continues to expand.")
    for size, alpha in ((320, 12), (240, 28), (160, 60)):
        offset = (320 - size) / 2
        _rect(market, 36 + offset, 120 + offset, size, size, NAVY, alpha_pct=alpha)
    _rect(market, 480, 120, 200, 320, GOLD, alpha_pct=12)
    _text(market, 480, 460, 400, 22, "Source: management estimates.", size=9, colour=GREY)
    _chrome(market, 2)

    # --- 3. company overview: an oversized frame, ink well inside it -------------
    #     18 of 20 slides blocked on the wordmark, and nothing spilled.
    company = _blank(presentation)
    _rect(company, 36, 30, 4, 24, GOLD)
    _text(company, 48, 36, 600, 24, "COMPANY OVERVIEW", size=14)
    _text(company, 36, 66, 880, 22, "Founded in 1998 and headquartered in Leeds.")
    _text(company, 36, 120, 426, 22, "Recurring revenue mix of 64 per cent.")
    _text(company, 36, 156, 426, 22, "Twelve hundred active industrial sites.")
    _text(company, 490, 120, 426, 22, "A direct sales force across four regions.")
    _text(company, 490, 156, 426, 22, "Average contract length of four years.")
    _text(company, 36, 460, 400, 22, "Source: company information.", size=9, colour=GREY)
    _chrome(company, 3)

    # --- 4. highlights: five identical blocks at one pitch -----------------------
    #     Two of their edges landed near a learned row and three did not, so
    #     LO-003 reported two of the five and breaking the set was the "fix".
    highlights = _blank(presentation)
    _rect(highlights, 36, 30, 4, 24, GOLD)
    _text(highlights, 48, 36, 600, 24, "INVESTMENT HIGHLIGHTS", size=14)
    _text(highlights, 36, 66, 880, 22, "Five reasons the asset is attractive.")
    for index in range(5):
        top = 108 + index * 70.56
        _rect(highlights, 36, top, 880, 61.92, GREY, alpha_pct=8)
        _text(highlights, 48, top + 8, 850, 22, f"Highlight number {index + 1}")
    _chrome(highlights, 4)

    # --- 5. a quadrant and a football field: shapes whose position is a number ---
    #     LO-003 proposed snapping a dot and a bar. The bar's 3.9pt snap would
    #     have restated $815M as about $821M.
    plotted = _blank(presentation)
    _rect(plotted, 36, 30, 4, 24, GOLD)
    _text(plotted, 48, 36, 600, 24, "COMPETITIVE POSITIONING", size=14)
    _rect(plotted, 36, 96, 400, 340, GREY, alpha_pct=6)
    for left, top in (
        (286.85, 235.73),
        (263.16, 313.56),
        (212.40, 222.19),
        (307.15, 347.40),
        (137.95, 415.08),
    ):
        _rect(plotted, left, top, 11.52, 11.52, NAVY)
    # A football field: three method names down a column, three bars whose
    # length is the value, and the figures printed at each end.
    for index, (bar_left, bar_width) in enumerate(
        ((678.56, 113.52), (719.84, 113.52), (702.64, 110.08))
    ):
        top = 154.8 + index * 67.68
        _text(plotted, 470, top, 198, 37.44, f"Method {index + 1}")
        _rect(plotted, bar_left, top, bar_width, 37.44, NAVY)
    _chrome(plotted, 5)

    # --- 6. one term in the two casings a deck legitimately uses -----------------
    #     Caps in the eyebrow, title case in prose. TY-005 reported every
    #     occurrence of whichever one it did not pick.
    segment = _blank(presentation)
    _rect(segment, 36, 30, 4, 24, GOLD)
    _text(segment, 48, 36, 600, 24, "KESTREL ANALYTICS", size=14)
    _text(segment, 36, 66, 880, 22, "Kestrel Analytics grew across the period.")
    _text(segment, 36, 102, 880, 22, "KESTREL ANALYTICS is set out overleaf.")
    _text(segment, 36, 138, 880, 22, "Kestrel Analytics is the platform segment.")
    _text(segment, 36, 174, 880, 22, "Kestrel Analytics carries the margin.")
    # --- 7. a padded field separator, deliberate and not a typing slip ----------
    _text(segment, 36, 210, 880, 22, "Revenue CAGR: 19.9%   |   EBITDA margin: 24.8%")
    _text(segment, 36, 460, 400, 22, "Source: management accounts.", size=9, colour=GREY)
    _chrome(segment, 6)

    presentation.save(str(path))
    return path


@pytest.fixture(scope="module")
def real_world_deck(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("real-world") / "constructions.pptx")


@pytest.fixture(scope="module")
def reviewed(real_world_deck: Path):
    clear_caches()
    deck = load_deck(str(real_world_deck))
    return learn_from_decks([deck], "kestrel")


def test_the_reference_review_reports_nothing_above_info(reviewed) -> None:
    """The whole point, in one assertion.

    A profile learned from these constructions must not report them. ``info`` is
    allowed: the bleed on slide 3 is reported there deliberately, so that a
    graphic leaving the canvas stays visible without gating a send.
    """
    gating = [f for f in reviewed.reference_review.findings if f.severity != "info"]
    assert not gating, "\n".join(
        f"{f.rule_id} [{f.severity}] slide {f.slide_index}: {f.message}" for f in gating
    )


def test_no_rule_crashed_while_reviewing(reviewed) -> None:
    """A rule that raised checked nothing, and would make the test above pass."""
    assert not reviewed.reference_review.failed_rules


def test_the_tint_ramp_stays_one_colour(reviewed) -> None:
    """The navy used at four opacities is one palette entry, not four breaches."""
    palette = {entry.upper() for entry in reviewed.profile.brand.palette_hex}
    assert "#0F2A4A" in palette


def test_both_casings_of_the_term_are_accepted(reviewed) -> None:
    accepted = reviewed.profile.typography.canon_accepted
    both = {
        canonical.casefold(): {form.casefold() for form in forms}
        for canonical, forms in accepted.items()
    }
    assert any(
        "kestrel analytics" in {canonical, *forms}
        for canonical, forms in both.items()
    ), f"neither casing was recorded as accepted: {accepted}"


def test_the_plotted_shapes_are_recognised_as_data(reviewed) -> None:
    """The quadrant's dots and the football field's bars are exempt on the axes
    that carry their value, and nothing else on the slide is."""
    from tieout.rules.layout import _data_series_axes

    slide = next(s for s in reviewed.decks[0].slides if s.index == 5)
    plotted = _data_series_axes(list(slide.leaf_shapes()), 2.0)
    assert plotted, "no data series was recognised on the quadrant slide"
    # The chrome is laid out, not plotted, on every slide it appears on.
    wordmark = next(
        shape for shape in slide.leaf_shapes() if shape.text.strip() == "KESTREL PARTNERS"
    )
    assert wordmark.ref.shape_id not in plotted


# --------------------------------------------------------------------------------------
# A logo that is drawn rather than placed
# --------------------------------------------------------------------------------------
#
# Furniture detection recognised a logo by the SHA1 of its image part, so a house
# mark shipped as vector artwork was not a logo at all: an autoshape with the
# monogram set on it and the wordmark beside it is three ordinary shapes as far
# as the file is concerned. Every fixture in this suite draws its logo with
# ``_logo_png``, which is the assumption the code was written from, so nothing
# noticed.
#
# The badge then entered ``content_shapes``, and on the one slide whose lockup is
# a different size -- a divider, which sets the mark larger -- its edge landed
# near the row the other slides' badges had established but not on it. The tool
# measured the logo against a grid derived from everything except the logo, and
# reported the difference.


#: The divider lockup's top edge, against a corner lockup pitched at 39.6pt. The
#: 3.6pt gap is the one the real deck produced: far enough to clear the grid's
#: own 2pt tolerance, near enough to sit inside the near-miss window.
DIVIDER_BADGE_TOP_PT = 36.0
FOOTER_TEXT = "Project Falcon  |  Strictly Private"
DIVIDER_BADGE_PT = 28.8


def _lockup(slide, left, top, badge_pt, monogram_pt, wordmark_pt):
    """The house mark as a brand team ships it in vector: badge, monogram, wordmark."""
    badge = slide.shapes.add_shape(
        MSO_SHAPE.HEXAGON, Pt(left), Pt(top), Pt(badge_pt), Pt(badge_pt)
    )
    badge.fill.solid()
    badge.fill.fore_color.rgb = GOLD
    badge.line.fill.background()
    _text(slide, left, top - 1.08, badge_pt, badge_pt, "H", size=monogram_pt)
    _text(
        slide,
        left + badge_pt * 1.52,
        top - 0.44,
        badge_pt * 5.75,
        badge_pt * 1.35,
        "HALYARD PARTNERS",
        size=wordmark_pt,
    )
    return badge


def _vector_chrome(slide, page: int) -> None:
    _lockup(slide, 808.78, 28.8, 18.72, 12, 6)
    _text(slide, 39.6, 509.76, 504, 17.28, FOOTER_TEXT, size=8, colour=GREY)
    _text(slide, 877.18, 509.76, 43.2, 17.28, str(page), size=8, colour=GREY)


def _build_vector_logo_deck(path: Path) -> Path:
    """Five content slides at one lockup size, and a divider at another."""
    presentation = _deck()

    for page, (eyebrow, headline) in enumerate(
        (
            ("EXECUTIVE SUMMARY", "Executive Summary"),
            ("EXECUTIVE SUMMARY", "Investment Highlights"),
            ("COMPANY OVERVIEW", "Company Overview"),
            ("MARKET OVERVIEW", "Market Overview and Growth"),
            ("VALUATION", "Valuation Overview"),
        ),
        start=1,
    ):
        slide = _blank(presentation)
        _text(slide, 39.6, 39.6, 300, 14, eyebrow, size=11, colour=GOLD)
        _text(slide, 39.6, 57.6, 880, 40, headline, size=29)
        _text(slide, 39.6, 120, 426, 22, "Recurring revenue mix of 64 per cent.", size=12.5)
        _text(slide, 39.6, 156, 426, 22, "Twelve hundred active industrial sites.")
        _text(slide, 500.4, 120, 420, 22, "A direct sales force across four regions.", size=14)
        _text(slide, 500.4, 156, 420, 22, "Average contract length of four years.")
        _text(slide, 39.6, 460, 400, 22, "Source: management information.", size=9, colour=GREY)
        _vector_chrome(slide, page)

    divider = _blank(presentation)
    _lockup(divider, 39.6, DIVIDER_BADGE_TOP_PT, DIVIDER_BADGE_PT, 18, 10)
    _text(divider, 39.6, 96.0, 576, 25.2, "SECTION 03", size=14, colour=GOLD)
    _text(divider, 39.6, 120.0, 756, 79.2, "Financial Performance and Valuation", size=29)
    _text(divider, 39.6, 200.0, 633.6, 36, "Historical results and the valuation framework.")
    _text(divider, 39.6, 240.0, 633.6, 36, "Recurring revenue mix of 64 per cent.")
    _text(divider, 39.6, 280.0, 633.6, 36, "Twelve hundred active industrial sites.")
    _text(divider, 39.6, 320.0, 633.6, 36, "A direct sales force across four regions.")
    _text(divider, 39.6, 509.76, 504, 17.28, FOOTER_TEXT, size=8, colour=GREY)
    _text(divider, 877.18, 509.76, 43.2, 17.28, "6", size=8, colour=GREY)

    presentation.save(str(path))
    return path


@pytest.fixture(scope="module")
def vector_logo_deck(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_vector_logo_deck(
        tmp_path_factory.mktemp("vector-logo") / "lockup.pptx"
    )


@pytest.fixture(scope="module")
def vector_logo_reviewed(vector_logo_deck: Path):
    clear_caches()
    deck = load_deck(str(vector_logo_deck))
    return learn_from_decks([deck], "halyard")


def test_a_drawn_logo_badge_is_chrome_on_every_slide(vector_logo_deck: Path) -> None:
    """The badge behind the monogram is part of the mark, not content.

    Asserted on the badge directly rather than through a rule, because every
    consequence -- the grid it skews, the margin it tightens, the title slot its
    monogram wins -- follows from this one classification.
    """
    from tieout.model.furniture import detect_furniture

    clear_caches()
    deck = load_deck(str(vector_logo_deck))
    furniture = detect_furniture(deck)

    content = [
        (slide.index, shape.ref.display_name)
        for slide in deck.slides
        for shape in slide.leaf_shapes()
        if not shape.has_text
        and shape.width_pt > 0
        and not furniture.is_furniture(slide.index, shape.ref.shape_id)
    ]
    assert not content, f"badge measured as content on: {content}"


def test_the_divider_lockup_is_not_reported_against_the_grid(vector_logo_reviewed) -> None:
    """The whole deck, learned and checked, reports nothing.

    Before the badge was chrome this said: "Hexagon 1 sits 3.6pt short of the
    learned grid: top at 36pt against the 39.6pt row" -- the tool asking for the
    house mark to be nudged onto a grid the house mark had no part in setting.
    """
    findings = [
        f"{f.rule_id} [{f.severity}] slide {f.slide_index}: {f.message}"
        for f in vector_logo_reviewed.reference_review.findings
    ]
    assert not findings, "\n".join(findings)
