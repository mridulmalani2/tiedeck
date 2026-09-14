"""Unit conversion, geometry and colour maths.

The least glamorous module in the project and the one with the widest blast
radius: every geometric finding in the tool is denominated in whatever these
functions return.
"""

from __future__ import annotations

import math

import pytest

from tieout.model import color as colour
from tieout.model.units import (
    EMU_PER_INCH,
    EMU_PER_POINT,
    approx_equal,
    degrees_to_ooxml_angle,
    emu_to_cm,
    emu_to_inches,
    emu_to_pt,
    hundredths_to_pt,
    inches_to_emu,
    inches_to_pt,
    ooxml_angle_to_degrees,
    pt_to_emu,
    pt_to_hundredths,
    pt_to_inches,
    rect_area_pt2,
    rect_intersection_area_pt2,
    rotated_bbox_pt,
    round_pt,
)


def test_the_emu_constants_are_the_ooxml_ones():
    assert EMU_PER_POINT == 12700
    assert EMU_PER_INCH == 914400
    assert EMU_PER_INCH == EMU_PER_POINT * 72


@pytest.mark.parametrize(
    ("emu", "points"),
    [(0, 0.0), (12700, 1.0), (914400, 72.0), (-12700, -1.0), (6350, 0.5)],
)
def test_emu_to_points(emu, points):
    assert emu_to_pt(emu) == pytest.approx(points)


def test_conversions_round_trip():
    for points in (0.0, 1.0, 7.2, 72.0, 540.0, 960.0):
        assert emu_to_pt(pt_to_emu(points)) == pytest.approx(points)
        assert inches_to_pt(pt_to_inches(points)) == pytest.approx(points)
    for emu in (0, 12700, 914400, 360000):
        assert inches_to_emu(emu_to_inches(emu)) == pytest.approx(emu, abs=1)


def test_none_propagates_rather_than_becoming_zero():
    """"Unspecified" and "zero" are different measurements, and collapsing the
    first into the second is how a missing position becomes a position."""
    assert emu_to_pt(None) is None
    assert pt_to_emu(None) is None
    assert hundredths_to_pt(None) is None
    assert pt_to_hundredths(None) is None
    assert emu_to_inches(None) is None
    assert pt_to_inches(None) is None
    assert emu_to_cm(None) is None
    assert round_pt(None) is None


def test_font_sizes_are_hundredths_of_a_point():
    assert hundredths_to_pt(1800) == 18.0
    assert hundredths_to_pt("700") == 7.0
    assert pt_to_hundredths(10.5) == 1050


def test_rotation_is_sixty_thousandths_of_a_degree():
    assert ooxml_angle_to_degrees(5400000) == 90.0
    assert ooxml_angle_to_degrees(None) == 0.0, "absent rotation is zero, not unknown"
    assert degrees_to_ooxml_angle(90.0) == 5400000


def test_approx_equal_never_treats_absence_as_equality():
    assert approx_equal(872.0, 872.4, 0.5)
    assert not approx_equal(872.0, 873.0, 0.5)
    assert not approx_equal(None, None, 99.0)
    assert not approx_equal(None, 1.0, 99.0)


def test_an_unrotated_box_is_returned_unchanged():
    box = (100.0, 50.0, 200.0, 80.0)
    assert rotated_bbox_pt(*box, 0.0) == box
    assert rotated_bbox_pt(*box, 360.0) == box


def test_a_quarter_turn_swaps_width_and_height_about_the_centre():
    """PowerPoint rotates about the shape's centre and leaves off/ext alone, so a
    rotated shape can overhang the canvas while its stored geometry does not."""
    left, top, width, height = rotated_bbox_pt(100.0, 100.0, 200.0, 100.0, 90.0)
    assert (width, height) == pytest.approx((100.0, 200.0))
    assert left + width / 2 == pytest.approx(200.0)
    assert top + height / 2 == pytest.approx(150.0)


def test_a_forty_five_degree_rotation_grows_the_bounding_box():
    _, _, width, height = rotated_bbox_pt(0.0, 0.0, 100.0, 100.0, 45.0)
    expected = 100.0 * math.sqrt(2)
    assert width == pytest.approx(expected)
    assert height == pytest.approx(expected)


def test_rectangle_overlap():
    a = (0.0, 0.0, 100.0, 100.0)
    assert rect_intersection_area_pt2(a, (50.0, 50.0, 100.0, 100.0)) == pytest.approx(2500.0)
    assert rect_intersection_area_pt2(a, (100.0, 0.0, 50.0, 50.0)) == 0.0, (
        "touching is not overlapping"
    )
    assert rect_intersection_area_pt2(a, (200.0, 200.0, 10.0, 10.0)) == 0.0
    assert rect_intersection_area_pt2(a, a) == pytest.approx(10000.0)
    assert rect_area_pt2((0.0, 0.0, -5.0, 10.0)) == 0.0


# --------------------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#1F3864", (0x1F, 0x38, 0x64)),
        ("1F3864", (0x1F, 0x38, 0x64)),
        ("#abc", (0xAA, 0xBB, 0xCC)),
        ("  #FFFFFF  ", (255, 255, 255)),
    ],
)
def test_hex_parsing(text, expected):
    parsed = colour.parse_hex(text)
    assert parsed.as_tuple() == expected


def test_malformed_colours_are_rejected_not_guessed():
    for bad in ("", "#12", "#GGGGGG", "navy", "#1234567"):
        with pytest.raises(colour.ColorParseError):
            colour.parse_hex(bad)
        assert colour.try_parse_hex(bad) is None
    assert colour.try_parse_hex(None) is None


def test_lab_endpoints():
    assert colour.rgb_to_lab(colour.Rgb(255, 255, 255)).lightness == pytest.approx(100.0, abs=1e-3)
    assert colour.rgb_to_lab(colour.Rgb(0, 0, 0)).lightness == pytest.approx(0.0, abs=1e-6)


def test_delta_e_treats_a_near_miss_as_the_same_colour():
    """The reason colour comparison is perceptual rather than string equality.

    #EB0A1E and #EA0A1F are different strings and the same colour to any reader,
    so a brand rule that fires on the second is a false positive.
    """
    a, b = colour.parse_hex("#EB0A1E"), colour.parse_hex("#EA0A1F")
    assert a.hex != b.hex
    assert colour.delta_e_76(a, b) < 1.0


def test_delta_e_separates_genuinely_different_colours():
    assert colour.delta_e_76(colour.parse_hex("#1F3864"), colour.parse_hex("#C00000")) > 30


def test_delta_e_is_zero_for_identical_colours_and_symmetric():
    navy = colour.parse_hex("#1F3864")
    grey = colour.parse_hex("#A6A6A6")
    assert colour.delta_e_76(navy, navy) == pytest.approx(0.0)
    assert colour.delta_e_76(navy, grey) == pytest.approx(colour.delta_e_76(grey, navy))


def test_nearest_palette_entry():
    palette = [colour.parse_hex(c) for c in ("#000000", "#FFFFFF", "#1F3864")]
    match = colour.nearest(colour.parse_hex("#1F3865"), palette)
    assert match is not None
    best, distance = match
    assert best.hex == "#1F3864"
    assert distance < 1.0
    assert colour.nearest(colour.parse_hex("#000000"), []) is None


def test_theme_lighter_variants_resolve_through_hsl():
    """lumMod 60% plus lumOff 40% is PowerPoint's "Lighter 40%".

    It only lands on the right colour when luminance is modified in HSL, which is
    why the transforms are split between HSL and linear RGB.
    """
    navy = colour.parse_hex("#1F3864")
    lighter = colour.apply_lum_off(colour.apply_lum_mod(navy, 0.6), 0.4)
    assert lighter.relative_luminance > navy.relative_luminance
    assert colour.rgb_to_lab(lighter).lightness > colour.rgb_to_lab(navy).lightness + 20


def test_tint_moves_toward_white_and_shade_toward_black():
    navy = colour.parse_hex("#1F3864")
    assert colour.apply_tint(navy, 0.4).relative_luminance > navy.relative_luminance
    assert colour.apply_shade(navy, 0.6).relative_luminance < navy.relative_luminance
    assert colour.apply_tint(navy, 0.0).hex == "#FFFFFF"
    assert colour.apply_shade(navy, 0.0).hex == "#000000"


def test_saturation_modifier():
    red = colour.parse_hex("#C00000")
    desaturated = colour.apply_sat_mod(red, 0.2)
    assert colour.delta_e_76(red, desaturated) > 5
    assert colour.apply_sat_mod(red, 1.0).hex == red.hex


def test_alpha_composites_over_a_backdrop():
    """A half-transparent brand colour measures as a different colour on screen,
    so flattening it against the page keeps the palette check honest."""
    navy = colour.parse_hex("#1F3864")
    over_white = colour.apply_alpha(navy, 0.5)
    assert over_white.relative_luminance > navy.relative_luminance
    assert colour.apply_alpha(navy, 1.0).hex == navy.hex


def test_unknown_transforms_pass_through_unchanged():
    """Aborting an entire deck's audit over a rare colour modifier would be a
    worse outcome than a slightly wrong measured colour."""
    navy = colour.parse_hex("#1F3864")
    assert colour.apply_transform(navy, "hueMod", 0.5).hex == navy.hex


def test_transfer_functions_round_trip():
    for value in (0.0, 0.04, 0.5, 0.9, 1.0):
        assert colour.linear_to_srgb(colour.srgb_to_linear(value)) == pytest.approx(
            value, abs=1e-9
        )
