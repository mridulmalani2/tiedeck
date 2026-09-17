"""Inheritance resolution: the eight cases section 6 requires.

This is the module the whole tool rests on. In a real deck a run's explicit font
is almost always absent, so a naive reader gets None, concludes the font is not
approved, and reports a brand violation on every correctly formatted slide. The
learning engine built on top of that derives garbage, because it observes "None"
as the dominant typeface.

Each case below isolates one level of the chain by building the OOXML for it
directly, so a regression names the level that broke rather than just failing
somewhere. Hand-built XML rather than generated decks precisely because a
generated deck exercises several levels at once.
"""

from __future__ import annotations

import io

import pytest
from lxml import etree
from pptx import Presentation
from pptx.util import Emu, Pt

from tieout.model.inherit import NS, ColorMap, SlideContext, Theme
from tieout.model.loader import _compose_group_transform, _Transform, load_deck

A = NS["a"]
P = NS["p"]


def _xml(text: str) -> etree._Element:
    return etree.fromstring(text.encode("utf-8"))


THEME = f"""
<a:theme xmlns:a="{A}" name="House">
  <a:themeElements>
    <a:clrScheme name="House">
      <a:dk1><a:srgbClr val="000000"/></a:dk1>
      <a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="1F3864"/></a:dk2>
      <a:lt2><a:srgbClr val="A6A6A6"/></a:lt2>
      <a:accent1><a:srgbClr val="1F3864"/></a:accent1>
      <a:accent2><a:srgbClr val="C00000"/></a:accent2>
      <a:accent3><a:srgbClr val="A6A6A6"/></a:accent3>
      <a:accent4><a:srgbClr val="1F3864"/></a:accent4>
      <a:accent5><a:srgbClr val="A6A6A6"/></a:accent5>
      <a:accent6><a:srgbClr val="C00000"/></a:accent6>
      <a:hlink><a:srgbClr val="1F3864"/></a:hlink>
      <a:folHlink><a:srgbClr val="A6A6A6"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="House">
      <a:majorFont><a:latin typeface="Gill Sans MT"/></a:majorFont>
      <a:minorFont><a:latin typeface="Arial"/></a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name="House">
      <a:fillStyleLst>
        <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
        <a:solidFill><a:srgbClr val="A6A6A6"/></a:solidFill>
      </a:fillStyleLst>
      <a:lnStyleLst>
        <a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
      </a:lnStyleLst>
      <a:bgFillStyleLst>
        <a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>
      </a:bgFillStyleLst>
    </a:fmtScheme>
  </a:themeElements>
</a:theme>
"""


def _master(color_map: str = '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
            'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
            'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>',
            body_size: int = 1100,
            body_colour: str = "333333") -> etree._Element:
    return _xml(f"""
    <p:sldMaster xmlns:p="{P}" xmlns:a="{A}">
      {color_map}
      <p:cSld name="House Master">
        <p:spTree>
          <p:sp>
            <p:nvSpPr>
              <p:cNvPr id="2" name="Title Placeholder"/>
              <p:cNvSpPr/>
              <p:nvPr><p:ph type="title"/></p:nvPr>
            </p:nvSpPr>
            <p:spPr><a:xfrm><a:off x="457200" y="274320"/>
              <a:ext cx="8229600" cy="1143000"/></a:xfrm></p:spPr>
            <p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody>
          </p:sp>
          <p:sp>
            <p:nvSpPr>
              <p:cNvPr id="3" name="Body Placeholder"/>
              <p:cNvSpPr/>
              <p:nvPr><p:ph type="body" idx="1"/></p:nvPr>
            </p:nvSpPr>
            <p:spPr><a:xfrm><a:off x="457200" y="1600200"/>
              <a:ext cx="8229600" cy="4525963"/></a:xfrm></p:spPr>
            <p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody>
          </p:sp>
        </p:spTree>
      </p:cSld>
      <p:txStyles>
        <p:titleStyle>
          <a:lvl1pPr><a:defRPr sz="2000" b="1">
            <a:solidFill><a:schemeClr val="tx2"/></a:solidFill>
            <a:latin typeface="+mj-lt"/></a:defRPr></a:lvl1pPr>
        </p:titleStyle>
        <p:bodyStyle>
          <a:lvl1pPr><a:defRPr sz="{body_size}">
            <a:solidFill><a:srgbClr val="{body_colour}"/></a:solidFill></a:defRPr></a:lvl1pPr>
          <a:lvl2pPr><a:defRPr sz="900" i="1"/></a:lvl2pPr>
        </p:bodyStyle>
        <p:otherStyle>
          <a:lvl1pPr><a:defRPr sz="1200"/></a:lvl1pPr>
        </p:otherStyle>
      </p:txStyles>
    </p:sldMaster>
    """)


def _layout(body_override: str = "") -> etree._Element:
    return _xml(f"""
    <p:sldLayout xmlns:p="{P}" xmlns:a="{A}" type="obj">
      <p:cSld name="Title and Content">
        <p:spTree>
          <p:sp>
            <p:nvSpPr>
              <p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/>
              <p:nvPr><p:ph type="title"/></p:nvPr>
            </p:nvSpPr>
            <p:spPr><a:xfrm><a:off x="457200" y="457200"/>
              <a:ext cx="11277600" cy="609600"/></a:xfrm></p:spPr>
            <p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody>
          </p:sp>
          <p:sp>
            <p:nvSpPr>
              <p:cNvPr id="3" name="Content Placeholder 2"/><p:cNvSpPr/>
              <p:nvPr><p:ph type="body" idx="1"/></p:nvPr>
            </p:nvSpPr>
            <p:spPr><a:xfrm><a:off x="457200" y="1524000"/>
              <a:ext cx="11277600" cy="4267200"/></a:xfrm></p:spPr>
            <p:txBody><a:bodyPr/>
              <a:lstStyle>{body_override}</a:lstStyle><a:p/></p:txBody>
          </p:sp>
        </p:spTree>
      </p:cSld>
    </p:sldLayout>
    """)


def _context(
    *,
    layout_body_override: str = "",
    color_map: str | None = None,
    master_body_size: int = 1100,
) -> SlideContext:
    master = (
        _master(color_map, body_size=master_body_size)
        if color_map is not None
        else _master(body_size=master_body_size)
    )
    return SlideContext(
        theme=Theme.parse(THEME.encode("utf-8")),
        color_map=ColorMap.parse(master),
        layout_el=_layout(layout_body_override),
        master_el=master,
    )


def _rpr(text: str) -> etree._Element:
    return _xml(f'<a:rPr xmlns:a="{A}" {text}')


# --------------------------------------------------------------------------------------
# 1. Run level explicit
# --------------------------------------------------------------------------------------


def test_run_level_properties_win_over_everything():
    context = _context()
    font = context.resolve_font(
        run_rpr=_rpr(
            'sz="1400" b="1" i="1"><a:solidFill><a:srgbClr val="C00000"/></a:solidFill>'
            '<a:latin typeface="Courier New"/></a:rPr>'
        ),
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert font.name == "Courier New"
    assert font.size_pt == 14.0
    assert font.bold is True
    assert font.italic is True
    assert font.color_hex == "#C00000"
    assert font.provenance["name"] == "run:rPr"
    assert font.provenance["size_pt"] == "run:rPr"


# --------------------------------------------------------------------------------------
# 2. Paragraph level inherited
# --------------------------------------------------------------------------------------


def test_paragraph_default_properties_are_used_when_the_run_is_silent():
    context = _context()
    ppr = _xml(
        f'<a:pPr xmlns:a="{A}"><a:defRPr sz="1300">'
        f'<a:solidFill><a:srgbClr val="123456"/></a:solidFill>'
        f'<a:latin typeface="Verdana"/></a:defRPr></a:pPr>'
    )
    font = context.resolve_font(
        run_rpr=None,
        para_ppr=ppr,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert (font.name, font.size_pt, font.color_hex) == ("Verdana", 13.0, "#123456")
    assert font.provenance["size_pt"] == "paragraph:defRPr"


def test_a_partial_run_falls_through_per_attribute():
    """Resolution is per attribute, not per level: a run that sets only its size
    still inherits its typeface and colour from further up."""
    context = _context()
    ppr = _xml(
        f'<a:pPr xmlns:a="{A}"><a:defRPr><a:latin typeface="Verdana"/></a:defRPr></a:pPr>'
    )
    font = context.resolve_font(
        run_rpr=_rpr('sz="1600"/>'),
        para_ppr=ppr,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert font.size_pt == 16.0
    assert font.provenance["size_pt"] == "run:rPr"
    assert font.name == "Verdana"
    assert font.provenance["name"] == "paragraph:defRPr"
    assert font.color_hex == "#333333"
    assert font.provenance["color_hex"] == "master:txStyles"


# --------------------------------------------------------------------------------------
# 3. Layout level inherited
# --------------------------------------------------------------------------------------


def test_the_layout_placeholder_is_consulted_before_the_master():
    context = _context(
        layout_body_override=(
            '<a:lvl1pPr><a:defRPr sz="1500">'
            '<a:solidFill><a:srgbClr val="00AA00"/></a:solidFill>'
            '<a:latin typeface="Tahoma"/></a:defRPr></a:lvl1pPr>'
        )
    )
    font = context.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert (font.name, font.size_pt, font.color_hex) == ("Tahoma", 15.0, "#00AA00")
    assert font.provenance["size_pt"] == "layout:placeholder"


def test_the_shape_list_style_outranks_the_layout():
    context = _context(
        layout_body_override='<a:lvl1pPr><a:defRPr sz="1500"/></a:lvl1pPr>'
    )
    font = context.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=0,
        shape_list_style=_xml(
            f'<a:lstStyle xmlns:a="{A}"><a:lvl1pPr><a:defRPr sz="1900"/>'
            f"</a:lvl1pPr></a:lstStyle>"
        ),
        ph_type="body",
        ph_idx=1,
    )
    assert font.size_pt == 19.0
    assert font.provenance["size_pt"] == "shape:lstStyle"


# --------------------------------------------------------------------------------------
# 4. Master level inherited
# --------------------------------------------------------------------------------------


def test_the_master_text_styles_supply_the_body_defaults():
    context = _context(master_body_size=1050)
    font = context.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert font.size_pt == 10.5
    assert font.color_hex == "#333333"
    assert font.provenance["size_pt"] == "master:txStyles"


def test_the_indent_level_selects_the_matching_master_level():
    context = _context()
    level_two = context.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=1,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert level_two.size_pt == 9.0
    assert level_two.italic is True


def test_a_level_beyond_those_defined_falls_back_to_the_deepest_defined():
    """What PowerPoint renders, and the alternative -- resolving nothing -- would
    report every deeply indented bullet as unsized."""
    context = _context()
    deep = context.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=6,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert deep.size_pt == 9.0


def test_a_title_uses_the_title_style_and_the_major_font():
    context = _context()
    font = context.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type="title",
        ph_idx=None,
    )
    assert font.size_pt == 20.0
    assert font.bold is True
    assert font.name == "Gill Sans MT", "+mj-lt must resolve to the major font"
    assert font.color_hex == "#1F3864", "tx2 maps to dk2 under the default colour map"


# --------------------------------------------------------------------------------------
# 5. Theme level inherited
# --------------------------------------------------------------------------------------


def test_the_theme_font_scheme_is_the_last_word_on_typeface():
    context = _context()
    body = context.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert body.name == "Arial", "body text takes the minor font"
    assert body.provenance["name"] == "theme:fontScheme"


def test_a_theme_token_anywhere_in_the_chain_resolves():
    context = _context()
    font = context.resolve_font(
        run_rpr=_rpr('><a:latin typeface="+mn-lt"/></a:rPr>'),
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert font.name == "Arial"


def test_powerpoint_defaults_apply_only_when_the_whole_chain_is_silent():
    bare = SlideContext(
        theme=Theme.empty(), color_map=ColorMap.parse(None), layout_el=None, master_el=None
    )
    font = bare.resolve_font(
        run_rpr=None,
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type=None,
        ph_idx=None,
    )
    assert font.size_pt == 18.0
    assert font.color_hex == "#000000"
    assert font.provenance["size_pt"] == "powerpoint:default"


# --------------------------------------------------------------------------------------
# 6. Scheme colour with lumMod
# --------------------------------------------------------------------------------------


def test_a_scheme_colour_with_luminance_modifiers_resolves():
    context = _context()
    font = context.resolve_font(
        run_rpr=_rpr(
            '><a:solidFill><a:schemeClr val="accent1">'
            '<a:lumMod val="60000"/><a:lumOff val="40000"/>'
            "</a:schemeClr></a:solidFill></a:rPr>"
        ),
        para_ppr=None,
        level=0,
        shape_list_style=None,
        ph_type="body",
        ph_idx=1,
    )
    assert font.color_hex is not None
    from tieout.model.color import parse_hex

    assert parse_hex(font.color_hex).relative_luminance > parse_hex(
        "#1F3864"
    ).relative_luminance, "Lighter 40% must be lighter than accent1 itself"


def test_transforms_apply_in_document_order():
    """lumMod then lumOff is a theme lightening; the reverse is another colour."""
    context = _context()
    forward = context.resolve_color(
        _xml(
            f'<a:solidFill xmlns:a="{A}"><a:schemeClr val="accent1">'
            f'<a:lumMod val="50000"/><a:lumOff val="50000"/></a:schemeClr></a:solidFill>'
        )
    )
    reverse = context.resolve_color(
        _xml(
            f'<a:solidFill xmlns:a="{A}"><a:schemeClr val="accent1">'
            f'<a:lumOff val="50000"/><a:lumMod val="50000"/></a:schemeClr></a:solidFill>'
        )
    )
    assert forward != reverse


def test_a_direct_srgb_colour_needs_no_scheme():
    context = _context()
    assert (
        context.resolve_color(
            _xml(f'<a:solidFill xmlns:a="{A}"><a:srgbClr val="ABCDEF"/></a:solidFill>')
        )
        == "#ABCDEF"
    )


def test_a_system_colour_uses_its_recorded_last_value():
    context = _context()
    assert (
        context.resolve_color(
            _xml(
                f'<a:solidFill xmlns:a="{A}">'
                f'<a:sysClr val="windowText" lastClr="202020"/></a:solidFill>'
            )
        )
        == "#202020"
    )


# --------------------------------------------------------------------------------------
# 7. Colour map swap inside a master
# --------------------------------------------------------------------------------------


def test_a_dark_master_swaps_background_and_text_through_its_colour_map():
    """The case that decides whether divider text is white on navy or navy on navy.

    A dark-background master maps bg1 to dk1 and tx1 to lt1, the inverse of a
    light master. Ignoring the map resolves every colour on such a slide to its
    opposite.
    """
    light = _context()
    dark = _context(
        color_map='<p:clrMap bg1="dk1" tx1="lt1" bg2="dk2" tx2="lt2" '
        'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
        'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    )
    fill = f'<a:solidFill xmlns:a="{A}"><a:schemeClr val="tx1"/></a:solidFill>'
    assert light.resolve_color(_xml(fill)) == "#000000"
    assert dark.resolve_color(_xml(fill)) == "#FFFFFF"

    background = f'<a:solidFill xmlns:a="{A}"><a:schemeClr val="bg1"/></a:solidFill>'
    assert light.resolve_color(_xml(background)) == "#FFFFFF"
    assert dark.resolve_color(_xml(background)) == "#000000"


def test_a_theme_slot_named_directly_bypasses_the_map():
    """dk1 and the accents name theme slots, so they must not be re-mapped."""
    dark = _context(
        color_map='<p:clrMap bg1="dk1" tx1="lt1" bg2="dk2" tx2="lt2" '
        'accent1="accent2" accent2="accent1" accent3="accent3" accent4="accent4" '
        'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    )
    assert (
        dark.resolve_color(
            _xml(f'<a:solidFill xmlns:a="{A}"><a:schemeClr val="dk1"/></a:solidFill>')
        )
        == "#000000"
    )
    assert (
        dark.resolve_color(
            _xml(f'<a:solidFill xmlns:a="{A}"><a:schemeClr val="accent1"/></a:solidFill>')
        )
        == "#C00000"
    ), "accent1 is remapped to accent2 by this master's colour map"


# --------------------------------------------------------------------------------------
# Fill and line, which follow the same chain
# --------------------------------------------------------------------------------------


def test_an_explicit_fill_wins_and_no_fill_is_distinct_from_absent():
    context = _context()
    solid = context.resolve_fill(
        _xml(f'<p:spPr xmlns:p="{P}" xmlns:a="{A}">'
             f'<a:solidFill><a:srgbClr val="1F3864"/></a:solidFill></p:spPr>')
    )
    assert solid.kind == "solid"
    assert solid.hex == "#1F3864"
    assert solid.is_solid

    none = context.resolve_fill(
        _xml(f'<p:spPr xmlns:p="{P}" xmlns:a="{A}"><a:noFill/></p:spPr>')
    )
    assert none.kind == "none"
    assert not none.is_solid

    unresolved = context.resolve_fill(
        _xml(f'<p:spPr xmlns:p="{P}" xmlns:a="{A}"/>')
    )
    assert unresolved.kind == "inherit", "no fill anywhere is not the same as noFill"


def test_a_style_reference_dereferences_into_the_theme_fill_list():
    """A shape whose fill comes from a:style/fillRef, with phClr supplying the
    colour. Reading only spPr would report such a shape as unfilled."""
    context = _context()
    fill = context.resolve_fill(
        _xml(f'<p:spPr xmlns:p="{P}" xmlns:a="{A}"/>'),
        style_el=_xml(
            f'<p:style xmlns:p="{P}" xmlns:a="{A}">'
            f'<a:fillRef idx="1"><a:srgbClr val="C00000"/></a:fillRef></p:style>'
        ),
    )
    assert fill.kind == "solid"
    assert fill.hex == "#C00000"
    assert "fillRef" in fill.source


def test_a_fill_reference_of_zero_means_no_fill():
    context = _context()
    fill = context.resolve_fill(
        _xml(f'<p:spPr xmlns:p="{P}" xmlns:a="{A}"/>'),
        style_el=_xml(f'<p:style xmlns:p="{P}" xmlns:a="{A}"><a:fillRef idx="0"/></p:style>'),
    )
    assert fill.kind == "none"


def test_a_gradient_reports_its_first_stop_and_says_it_is_a_gradient():
    """Reducing a gradient to one colour silently would make a rule treat it as a
    flat fill, so the kind travels with the value."""
    context = _context()
    fill = context.resolve_fill(
        _xml(
            f'<p:spPr xmlns:p="{P}" xmlns:a="{A}"><a:gradFill><a:gsLst>'
            f'<a:gs pos="0"><a:srgbClr val="1F3864"/></a:gs>'
            f'<a:gs pos="100000"><a:srgbClr val="FFFFFF"/></a:gs>'
            f"</a:gsLst></a:gradFill></p:spPr>"
        )
    )
    assert fill.kind == "gradient"
    assert fill.hex == "#1F3864"
    assert not fill.is_solid


def test_line_width_and_colour_resolve_together():
    context = _context()
    line = context.resolve_line(
        _xml(
            f'<p:spPr xmlns:p="{P}" xmlns:a="{A}"><a:ln w="19050">'
            f'<a:solidFill><a:srgbClr val="A6A6A6"/></a:solidFill></a:ln></p:spPr>'
        )
    )
    assert line.kind == "solid"
    assert line.hex == "#A6A6A6"
    assert line.width_pt == pytest.approx(1.5)
    assert line.is_visible

    invisible = context.resolve_line(
        _xml(f'<p:spPr xmlns:p="{P}" xmlns:a="{A}"><a:ln><a:noFill/></a:ln></p:spPr>')
    )
    assert invisible.kind == "none"
    assert not invisible.is_visible


# --------------------------------------------------------------------------------------
# 8. Nested groups with non-identity child scaling
# --------------------------------------------------------------------------------------


def test_a_group_transform_offsets_and_scales_child_coordinates():
    element = _xml(f"""
      <p:grpSp xmlns:p="{P}" xmlns:a="{A}">
        <p:grpSpPr><a:xfrm>
          <a:off x="1270000" y="635000"/>
          <a:ext cx="2540000" cy="1270000"/>
          <a:chOff x="0" y="0"/>
          <a:chExt cx="1270000" cy="635000"/>
        </a:xfrm></p:grpSpPr>
      </p:grpSp>
    """)
    transform = _compose_group_transform(element, None)
    assert transform.scale_x == pytest.approx(2.0)
    assert transform.scale_y == pytest.approx(2.0)
    # A child at the group's own child-space origin lands at the group's position.
    assert transform.apply_point(0.0, 0.0) == pytest.approx((100.0, 50.0))
    assert transform.apply_size(10.0, 10.0) == pytest.approx((20.0, 20.0))


def test_a_non_zero_child_offset_is_subtracted_before_scaling():
    element = _xml(f"""
      <p:grpSp xmlns:p="{P}" xmlns:a="{A}">
        <p:grpSpPr><a:xfrm>
          <a:off x="1270000" y="0"/>
          <a:ext cx="1270000" cy="1270000"/>
          <a:chOff x="635000" y="0"/>
          <a:chExt cx="1270000" cy="1270000"/>
        </a:xfrm></p:grpSpPr>
      </p:grpSp>
    """)
    transform = _compose_group_transform(element, None)
    assert transform.scale_x == pytest.approx(1.0)
    # A child authored at chOff sits at the group's off, not at off + chOff.
    assert transform.apply_point(50.0, 0.0) == pytest.approx((100.0, 0.0))


def test_transforms_compose_so_nested_groups_resolve():
    outer = _Transform(offset_x=100.0, offset_y=0.0, scale_x=2.0, scale_y=2.0)
    inner = _Transform(offset_x=10.0, offset_y=5.0, scale_x=3.0, scale_y=3.0)
    combined = outer.compose(inner)
    assert combined.scale_x == pytest.approx(6.0)
    assert combined.offset_x == pytest.approx(120.0)
    assert combined.apply_point(1.0, 1.0) == pytest.approx(
        outer.apply_point(*inner.apply_point(1.0, 1.0))
    )


def test_a_group_with_no_transform_passes_the_outer_one_through():
    outer = _Transform(offset_x=7.0, offset_y=9.0, scale_x=2.0, scale_y=2.0)
    element = _xml(f'<p:grpSp xmlns:p="{P}" xmlns:a="{A}"><p:grpSpPr/></p:grpSp>')
    assert _compose_group_transform(element, outer) is outer
    assert _compose_group_transform(element, None).scale_x == 1.0


def test_group_geometry_is_reported_in_true_slide_space(tmp_path):
    """End to end: a scaled group in a real package.

    Groups are how bankers build callout boxes and bridge charts, which are
    exactly the shapes that most often drift, so reporting their contents in
    child-space coordinates would make the findings unusable.
    """
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    group = slide.shapes.add_group_shape()
    group.name = "Callout group"
    inner = group.shapes.add_textbox(Pt(0), Pt(0), Pt(100), Pt(50))
    inner.name = "Inner box"
    inner.text_frame.text = "inside a scaled group"

    # Author the group at twice its children's coordinate space, offset to (200, 100).
    xfrm = group._element.find("p:grpSpPr/a:xfrm", NS)
    for tag, attrs in (
        ("off", {"x": str(200 * 12700), "y": str(100 * 12700)}),
        ("ext", {"cx": str(200 * 12700), "cy": str(100 * 12700)}),
        ("chOff", {"x": "0", "y": "0"}),
        ("chExt", {"cx": str(100 * 12700), "cy": str(50 * 12700)}),
    ):
        node = xfrm.find(f"a:{tag}", NS)
        if node is None:
            node = etree.SubElement(xfrm, f"{{{A}}}{tag}")
        for key, value in attrs.items():
            node.set(key, value)

    path = tmp_path / "group.pptx"
    presentation.save(str(path))

    deck = load_deck(path)
    inner_model = next(
        shape for shape in deck.slides[0].all_shapes() if shape.ref.name == "Inner box"
    )
    assert inner_model.ref.group_path == ("Callout group",)
    assert inner_model.left_pt == pytest.approx(200.0)
    assert inner_model.top_pt == pytest.approx(100.0)
    assert inner_model.width_pt == pytest.approx(200.0), "scaled 2x by the group"
    assert inner_model.height_pt == pytest.approx(100.0)


def test_an_image_placed_in_a_group_keeps_its_identity(tmp_path):
    """A logo inside a group must still be found by hash and reported in slide
    space, because templates frequently group the logo with a rule or a strapline."""
    from PIL import Image

    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    buffer = io.BytesIO()
    Image.new("RGB", (288, 96), (31, 56, 100)).save(buffer, "PNG")
    buffer.seek(0)
    picture = group.shapes.add_picture(buffer, Pt(852), Pt(24), Pt(72), Pt(24))
    picture.name = "Logo"

    path = tmp_path / "grouped-logo.pptx"
    presentation.save(str(path))

    deck = load_deck(path)
    logos = [s for s in deck.slides[0].all_shapes() if s.image_sha1]
    assert len(logos) == 1
    assert logos[0].ref.group_path, "the group path must be recorded"
    assert logos[0].left_pt == pytest.approx(852.0, abs=0.5)
    assert logos[0].image_pixel_width == 288


# --------------------------------------------------------------------------------------
# The transforms match what Office actually renders
# --------------------------------------------------------------------------------------


def test_theme_variants_match_office_s_documented_outputs():
    """Every Office theme ships Accent 1 as 4472C4 and its five variants as fixed
    lumMod/lumOff pairs, and the colours those render to are documented. A
    palette rule compares against these, so a transform applied in the wrong
    colour space -- luminance in RGB rather than HSL, tint in sRGB rather than
    linear -- would read every "Lighter 40%" fill as off-palette."""
    from tieout.model import color as colour

    base = colour.parse_hex("4472C4")
    variants = [
        ("Lighter 80%", (("lumMod", 0.20), ("lumOff", 0.80)), "D9E2F3"),
        ("Lighter 60%", (("lumMod", 0.40), ("lumOff", 0.60)), "B4C7E7"),
        ("Lighter 40%", (("lumMod", 0.60), ("lumOff", 0.40)), "8FAADC"),
        ("Darker 25%", (("lumMod", 0.75),), "2F5597"),
        ("Darker 50%", (("lumMod", 0.50),), "1F3864"),
    ]
    for name, steps, expected in variants:
        result = base
        for tag, amount in steps:
            result = colour.apply_transform(result, tag, amount)
        distance = colour.delta_e_76(result, colour.parse_hex(expected))
        assert distance < 1.5, (
            f"{name}: got {result.r:02X}{result.g:02X}{result.b:02X}, Office renders "
            f"{expected} (Delta-E {distance:.2f})"
        )
