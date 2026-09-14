"""OOXML font, fill, line and colour inheritance resolution.

This is the single largest correctness risk in TieOut, and everything downstream
depends on it being right.

In a real deck a run's explicit font is almost always absent. Open any
banker-authored slide and the XML for a body bullet is frequently just
``<a:r><a:t>text</a:t></a:r>`` with no ``a:rPr`` at all. The typeface, size and
colour come from a chain that reaches up through the paragraph, the shape's list
style, the layout placeholder, the master placeholder, the master's text styles
and finally the theme's font scheme.

A naive implementation that reads ``run.font.name`` gets None, concludes the font
is not in the approved set, and reports a brand violation on every correctly
formatted slide in the deck. The learning engine built on top of it derives
garbage, because it observes "None" as the dominant typeface.

So: resolve first, measure second. Nothing outside this module is permitted to
read an unresolved text property.

Resolution order for font name, size, bold, italic and colour, first non-absent
value winning:

1. run properties ``a:rPr``
2. paragraph properties ``a:pPr/a:defRPr``
3. the shape's text body list style ``a:lstStyle`` at the matching indent level
4. the layout placeholder with the same ``idx``, else the same ``type``
5. the master placeholder with the same ``type``
6. master ``p:txStyles`` (``p:titleStyle``/``p:bodyStyle``/``p:otherStyle``) at level
7. the presentation's ``p:defaultTextStyle`` at level
8. theme ``a:fontScheme``, major for titles and minor for body

Step 7 is not in the specification's list but is genuinely part of PowerPoint's
chain: a plain text box drawn on a slide inherits from ``p:defaultTextStyle``, not
from the master's ``p:otherStyle``. Omitting it makes every text box in a deck
resolve to the theme default size, which is usually wrong by several points.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Final

from lxml import etree

from tieout.model import color as colour
from tieout.model.units import emu_to_pt, hundredths_to_pt

A: Final[str] = "http://schemas.openxmlformats.org/drawingml/2006/main"
P: Final[str] = "http://schemas.openxmlformats.org/presentationml/2006/main"
R: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

NS: Final[dict[str, str]] = {"a": A, "p": P, "r": R}

#: The nine indent levels OOXML allows.
MAX_LEVEL: Final[int] = 8

#: DrawingML colour transform elements, in the order they may appear.
_TRANSFORM_TAGS: Final[tuple[str, ...]] = (
    "tint",
    "shade",
    "lumMod",
    "lumOff",
    "satMod",
    "alpha",
)

#: Theme colour slots as they appear in ``a:clrScheme``.
_THEME_SLOTS: Final[tuple[str, ...]] = (
    "dk1",
    "lt1",
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
)

#: Placeholder types that take the master's ``p:titleStyle``.
TITLE_PH_TYPES: Final[frozenset[str]] = frozenset({"title", "ctrTitle"})

#: Placeholder types that take the master's ``p:bodyStyle``.
BODY_PH_TYPES: Final[frozenset[str]] = frozenset({"body", "subTitle", "obj", "tbl", "chart"})

#: Master placeholder types, which are fewer than layout placeholder types. A
#: layout ``subTitle`` or ``obj`` inherits from the master ``body`` placeholder.
_MASTER_PH_EQUIVALENT: Final[dict[str, str]] = {
    "ctrTitle": "title",
    "title": "title",
    "subTitle": "body",
    "obj": "body",
    "body": "body",
    "tbl": "body",
    "chart": "body",
    "dgm": "body",
    "media": "body",
    "clipArt": "body",
    "pic": "body",
    "dt": "dt",
    "ftr": "ftr",
    "sldNum": "sldNum",
    "hdr": "ftr",
    "sldImg": "body",
}

#: PowerPoint's own hard defaults, used only when the whole chain is silent.
_FALLBACK_SIZE_PT: Final[float] = 18.0
_FALLBACK_COLOR: Final[str] = "#000000"


def qn(tag: str) -> str:
    """``a:rPr`` -> the fully qualified lxml tag name."""
    prefix, _, local = tag.partition(":")
    return f"{{{NS[prefix]}}}{local}"


def _bool_attr(element: etree._Element | None, name: str) -> bool | None:
    """Read an OOXML boolean attribute, which may be ``1``/``0``/``true``/``false``."""
    if element is None:
        return None
    raw = element.get(name)
    if raw is None:
        return None
    return raw in ("1", "true", "True", "on")


# --------------------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Theme:
    """The parts of a theme part that participate in inheritance."""

    major_latin: str | None
    minor_latin: str | None
    #: Slot name -> the colour-bearing element (``a:srgbClr``/``a:sysClr``/...).
    scheme: dict[str, etree._Element]
    fill_styles: tuple[etree._Element, ...]
    line_styles: tuple[etree._Element, ...]
    bg_fill_styles: tuple[etree._Element, ...]
    name: str | None = None

    @staticmethod
    def parse(theme_xml: bytes | etree._Element) -> Theme:
        root = (
            theme_xml
            if isinstance(theme_xml, etree._Element)
            else etree.fromstring(theme_xml)
        )
        scheme: dict[str, etree._Element] = {}
        for slot in _THEME_SLOTS:
            node = root.find(f".//a:themeElements/a:clrScheme/a:{slot}", NS)
            if node is not None:
                child = _first_color_child(node)
                if child is not None:
                    scheme[slot] = child

        return Theme(
            major_latin=_typeface(root, "majorFont"),
            minor_latin=_typeface(root, "minorFont"),
            scheme=scheme,
            fill_styles=tuple(root.findall(".//a:fmtScheme/a:fillStyleLst/*", NS)),
            line_styles=tuple(root.findall(".//a:fmtScheme/a:lnStyleLst/*", NS)),
            bg_fill_styles=tuple(root.findall(".//a:fmtScheme/a:bgFillStyleLst/*", NS)),
            name=root.get("name"),
        )

    @staticmethod
    def empty() -> Theme:
        """A theme that resolves nothing. Used when a package has no theme part,
        which happens with decks produced by some non-Microsoft generators."""
        return Theme(None, None, {}, (), (), ())

    def latin(self, *, major: bool) -> str | None:
        return self.major_latin if major else self.minor_latin


def _typeface(root: etree._Element, which: str) -> str | None:
    node = root.find(f".//a:themeElements/a:fontScheme/a:{which}/a:latin", NS)
    if node is None:
        return None
    face = node.get("typeface")
    return face or None


def _first_color_child(parent: etree._Element) -> etree._Element | None:
    """The colour-bearing child of a fill or scheme slot, if any."""
    for tag in ("srgbClr", "schemeClr", "sysClr", "prstClr", "hslClr", "scrgbClr"):
        node = parent.find(f"a:{tag}", NS)
        if node is not None:
            return node
    return None


# --------------------------------------------------------------------------------------
# Colour map
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColorMap:
    """A master's ``p:clrMap``, which indirects scheme colour names to theme slots.

    A dark-background master maps ``bg1`` to ``dk1`` and ``tx1`` to ``lt1``, the
    inverse of a light master. Ignoring the map makes every colour on a dark
    divider slide resolve to its opposite, which is the difference between "the
    section divider text is white on navy" and "the section divider text is navy
    on navy".
    """

    mapping: dict[str, str]

    @staticmethod
    def parse(master_el: etree._Element | None) -> ColorMap:
        default = {
            "bg1": "lt1",
            "tx1": "dk1",
            "bg2": "lt2",
            "tx2": "dk2",
            "accent1": "accent1",
            "accent2": "accent2",
            "accent3": "accent3",
            "accent4": "accent4",
            "accent5": "accent5",
            "accent6": "accent6",
            "hlink": "hlink",
            "folHlink": "folHlink",
        }
        if master_el is None:
            return ColorMap(default)
        node = master_el.find("p:clrMap", NS)
        if node is None:
            return ColorMap(default)
        mapping = dict(default)
        for key in default:
            value = node.get(key)
            if value:
                mapping[key] = value
        return ColorMap(mapping)

    def slot_for(self, scheme_name: str) -> str:
        """Map a scheme colour name to a theme slot.

        ``bg1``/``tx1``/``bg2``/``tx2`` go through the map. ``dk1``/``lt1`` and the
        accents name theme slots directly and pass through unchanged.
        """
        return self.mapping.get(scheme_name, scheme_name)


# --------------------------------------------------------------------------------------
# Resolved values
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedFont:
    """A fully resolved run font. Every field is a measurement, not an assertion."""

    name: str | None
    size_pt: float | None
    bold: bool | None
    italic: bool | None
    underline: bool | None
    color_hex: str | None
    #: Attribute name -> the chain level that supplied it, for debugging and for
    #: explaining a finding to a user who insists the slide "looks fine".
    provenance: dict[str, str] = field(default_factory=dict)

    @property
    def rgb(self) -> colour.Rgb | None:
        return colour.try_parse_hex(self.color_hex)


@dataclass(frozen=True, slots=True)
class ResolvedFill:
    """A resolved shape fill.

    ``kind`` distinguishes "explicitly no fill" from "a solid colour" from "a
    gradient TieOut does not reduce to one colour". A rule that treats a gradient
    as a missing fill produces nonsense, so the kind is always carried.
    """

    kind: str  # solid | none | gradient | picture | pattern | group | inherit
    hex: str | None
    source: str = ""

    @property
    def rgb(self) -> colour.Rgb | None:
        return colour.try_parse_hex(self.hex)

    @property
    def is_solid(self) -> bool:
        return self.kind == "solid" and self.hex is not None


@dataclass(frozen=True, slots=True)
class ResolvedLine:
    """A resolved shape outline."""

    kind: str  # solid | none | gradient | pattern | inherit
    hex: str | None
    width_pt: float | None
    source: str = ""

    @property
    def rgb(self) -> colour.Rgb | None:
        return colour.try_parse_hex(self.hex)

    @property
    def is_visible(self) -> bool:
        """An outline that actually draws. A zero-width solid line still draws
        a hairline in PowerPoint, so width is not part of the test."""
        return self.kind in ("solid", "gradient", "pattern")


# --------------------------------------------------------------------------------------
# Slide context
# --------------------------------------------------------------------------------------


@dataclass
class SlideContext:
    """Everything needed to resolve properties for shapes on one slide.

    Built once per slide by the loader, then consulted per shape and per run.
    Placeholder lookups are memoised because a dense table slide resolves the
    same layout placeholder hundreds of times.
    """

    theme: Theme
    color_map: ColorMap
    layout_el: etree._Element | None
    master_el: etree._Element | None
    default_text_style: etree._Element | None = None
    _layout_ph_cache: dict[tuple[str | None, int | None], etree._Element | None] = field(
        default_factory=dict, repr=False
    )
    _master_ph_cache: dict[str | None, etree._Element | None] = field(
        default_factory=dict, repr=False
    )

    # -- colour ------------------------------------------------------------------

    def resolve_color(
        self,
        container: etree._Element | None,
        *,
        ph_color: etree._Element | None = None,
        _depth: int = 0,
    ) -> str | None:
        """Resolve a colour-bearing container (``a:solidFill``, ``a:fgClr``, a theme
        scheme slot) to a final sRGB hex string.

        ``ph_color`` supplies the value of ``a:phClr``, which appears inside theme
        style lists and means "whatever colour the referencing shape asked for".
        """
        if container is None or _depth > 8:
            return None
        node = _first_color_child(container)
        if node is None:
            return None
        return self._resolve_color_node(node, ph_color=ph_color, depth=_depth)

    def _resolve_color_node(
        self,
        node: etree._Element,
        *,
        ph_color: etree._Element | None,
        depth: int,
    ) -> str | None:
        if depth > 8:
            return None
        local = etree.QName(node).localname
        base: colour.Rgb | None = None

        if local == "srgbClr":
            base = colour.try_parse_hex(node.get("val"))
        elif local == "sysClr":
            # lastClr records what the host OS resolved the system colour to when
            # the file was saved. It is the only portable reading of sysClr.
            base = colour.try_parse_hex(node.get("lastClr")) or _SYS_CLR_FALLBACK.get(
                node.get("val") or "", None
            )
        elif local == "prstClr":
            base = _PRESET_COLORS.get(node.get("val") or "")
        elif local == "hslClr":
            base = _hsl_attr_to_rgb(node)
        elif local == "scrgbClr":
            base = _scrgb_attr_to_rgb(node)
        elif local == "schemeClr":
            base = self._resolve_scheme_color(node, ph_color=ph_color, depth=depth)

        if base is None:
            return None
        return _apply_transforms(base, node).hex

    def _resolve_scheme_color(
        self,
        node: etree._Element,
        *,
        ph_color: etree._Element | None,
        depth: int,
    ) -> colour.Rgb | None:
        name = node.get("val") or ""
        if name == "phClr":
            if ph_color is None:
                return None
            resolved = self._resolve_color_node(ph_color, ph_color=None, depth=depth + 1)
            return colour.try_parse_hex(resolved)
        slot = self.color_map.slot_for(name)
        target = self.theme.scheme.get(slot)
        if target is None:
            return None
        resolved = self._resolve_color_node(target, ph_color=ph_color, depth=depth + 1)
        return colour.try_parse_hex(resolved)

    # -- placeholder lookup ------------------------------------------------------

    def layout_placeholder(
        self, ph_type: str | None, ph_idx: int | None
    ) -> etree._Element | None:
        """The layout placeholder matching ``idx``, else matching ``type``."""
        key = (ph_type, ph_idx)
        if key in self._layout_ph_cache:
            return self._layout_ph_cache[key]
        found = _find_placeholder(self.layout_el, ph_type, ph_idx)
        self._layout_ph_cache[key] = found
        return found

    def master_placeholder(self, ph_type: str | None) -> etree._Element | None:
        """The master placeholder for the equivalent master type."""
        if ph_type in self._master_ph_cache:
            return self._master_ph_cache[ph_type]
        equivalent = _MASTER_PH_EQUIVALENT.get(ph_type or "", "body" if ph_type else None)
        found = _find_placeholder(self.master_el, equivalent, None) if equivalent else None
        self._master_ph_cache[ph_type] = found
        return found

    # -- font --------------------------------------------------------------------

    def resolve_font(
        self,
        *,
        run_rpr: etree._Element | None,
        para_ppr: etree._Element | None,
        level: int,
        shape_list_style: etree._Element | None,
        ph_type: str | None,
        ph_idx: int | None,
        is_text_box: bool = False,
        body_pr_normalise: float | None = None,
    ) -> ResolvedFont:
        """Resolve one run's font through the full inheritance chain.

        ``body_pr_normalise`` is the ``a:normAutofit/@fontScale`` fraction on the
        shape's body properties. PowerPoint's "shrink text on overflow" scales the
        rendered size without touching ``sz``, so a 14pt run in an autofit shape at
        92% scale renders at 12.88pt. The rendered size is what a reader measures,
        so it is what TieOut reports.
        """
        level = max(0, min(level, MAX_LEVEL))
        sources = self._font_sources(
            run_rpr=run_rpr,
            para_ppr=para_ppr,
            level=level,
            shape_list_style=shape_list_style,
            ph_type=ph_type,
            ph_idx=ph_idx,
            is_text_box=is_text_box,
        )

        name: str | None = None
        size_pt: float | None = None
        bold: bool | None = None
        italic: bool | None = None
        underline: bool | None = None
        color_hex: str | None = None
        provenance: dict[str, str] = {}

        for label, element in sources:
            if element is None:
                continue
            if name is None:
                candidate = _latin_typeface(element)
                if candidate is not None:
                    name = candidate
                    provenance["name"] = label
            if size_pt is None:
                candidate_size = hundredths_to_pt(element.get("sz"))
                if candidate_size is not None:
                    size_pt = candidate_size
                    provenance["size_pt"] = label
            if bold is None:
                candidate_bold = _bool_attr(element, "b")
                if candidate_bold is not None:
                    bold = candidate_bold
                    provenance["bold"] = label
            if italic is None:
                candidate_italic = _bool_attr(element, "i")
                if candidate_italic is not None:
                    italic = candidate_italic
                    provenance["italic"] = label
            if underline is None:
                underline_raw = element.get("u")
                if underline_raw is not None:
                    underline = underline_raw != "none"
                    provenance["underline"] = label
            if color_hex is None:
                candidate_color = self._text_color(element)
                if candidate_color is not None:
                    color_hex = candidate_color
                    provenance["color_hex"] = label

        # Theme font scheme is the last word on typeface, and is also what a
        # "+mj-lt"/"+mn-lt" token anywhere in the chain resolves to.
        major = (ph_type or "") in TITLE_PH_TYPES
        if name is None or name.startswith("+"):
            token = name
            name = self._theme_face(token, major=major)
            provenance["name"] = "theme:fontScheme"

        if size_pt is None:
            size_pt = _FALLBACK_SIZE_PT
            provenance["size_pt"] = "powerpoint:default"
        if color_hex is None:
            color_hex = _FALLBACK_COLOR
            provenance["color_hex"] = "powerpoint:default"

        if body_pr_normalise is not None and size_pt is not None:
            size_pt = round(size_pt * body_pr_normalise, 4)
            provenance["size_pt"] = provenance.get("size_pt", "") + "+normAutofit"

        return ResolvedFont(
            name=name,
            size_pt=size_pt,
            bold=bold,
            italic=italic,
            underline=underline,
            color_hex=color_hex,
            provenance=provenance,
        )

    def _theme_face(self, token: str | None, *, major: bool) -> str | None:
        """Resolve a ``+mj-lt``/``+mn-lt`` token, or supply the scheme default."""
        if token == "+mj-lt":
            return self.theme.major_latin
        if token == "+mn-lt":
            return self.theme.minor_latin
        return self.theme.latin(major=major)

    def _font_sources(
        self,
        *,
        run_rpr: etree._Element | None,
        para_ppr: etree._Element | None,
        level: int,
        shape_list_style: etree._Element | None,
        ph_type: str | None,
        ph_idx: int | None,
        is_text_box: bool,
    ) -> list[tuple[str, etree._Element | None]]:
        """Assemble the chain as (label, rPr-like element) pairs, highest first."""
        sources: list[tuple[str, etree._Element | None]] = [
            ("run:rPr", run_rpr),
            ("paragraph:defRPr", _def_rpr(para_ppr)),
            ("shape:lstStyle", _level_def_rpr(shape_list_style, level)),
        ]

        layout_ph = self.layout_placeholder(ph_type, ph_idx)
        if layout_ph is not None:
            sources.append(
                ("layout:placeholder", _level_def_rpr(_list_style_of(layout_ph), level))
            )
            sources.append(
                ("layout:placeholder:pPr", _def_rpr(_first_para_ppr(layout_ph)))
            )

        master_ph = self.master_placeholder(ph_type)
        if master_ph is not None:
            sources.append(
                ("master:placeholder", _level_def_rpr(_list_style_of(master_ph), level))
            )

        master_style = self._master_text_style(ph_type, is_text_box=is_text_box)
        if master_style is not None:
            sources.append(("master:txStyles", _level_def_rpr(master_style, level)))

        if self.default_text_style is not None:
            sources.append(
                (
                    "presentation:defaultTextStyle",
                    _level_def_rpr(self.default_text_style, level),
                )
            )
        return sources

    def _master_text_style(
        self, ph_type: str | None, *, is_text_box: bool
    ) -> etree._Element | None:
        """Pick ``p:titleStyle``, ``p:bodyStyle`` or ``p:otherStyle``.

        A plain text box takes none of them -- it inherits from the presentation's
        default text style. An autoshape carrying text takes ``p:otherStyle``.
        """
        if self.master_el is None:
            return None
        if ph_type in TITLE_PH_TYPES:
            which = "p:titleStyle"
        elif ph_type in BODY_PH_TYPES:
            which = "p:bodyStyle"
        elif ph_type is not None:
            which = "p:otherStyle"
        elif is_text_box:
            return None
        else:
            which = "p:otherStyle"
        return self.master_el.find(f"p:txStyles/{which}", NS)

    def _text_color(self, rpr: etree._Element) -> str | None:
        """A run's colour lives in ``a:solidFill``; other fill kinds are not a
        single colour and are reported as absent rather than guessed at."""
        solid = rpr.find("a:solidFill", NS)
        if solid is not None:
            return self.resolve_color(solid)
        if rpr.find("a:noFill", NS) is not None:
            return None
        return None

    # -- fill --------------------------------------------------------------------

    def resolve_fill(
        self,
        sp_pr: etree._Element | None,
        *,
        style_el: etree._Element | None = None,
        ph_type: str | None = None,
        ph_idx: int | None = None,
    ) -> ResolvedFill:
        """Resolve a shape's fill through shape -> style -> layout -> master -> theme."""
        direct = _read_fill(sp_pr)
        if direct is not None:
            return self._materialise_fill(direct, "shape:spPr", ph_color=None)

        ref = self._style_fill_ref(style_el)
        if ref is not None:
            fill_el, ph_color = ref
            return self._materialise_fill(fill_el, "shape:style/fillRef", ph_color=ph_color)

        for label, source in self._inherited_shape_sources(ph_type, ph_idx):
            inherited = _read_fill(_sp_pr_of(source))
            if inherited is not None:
                return self._materialise_fill(inherited, label, ph_color=None)
            inherited_ref = self._style_fill_ref(_style_of(source))
            if inherited_ref is not None:
                fill_el, ph_color = inherited_ref
                return self._materialise_fill(
                    fill_el, f"{label}/fillRef", ph_color=ph_color
                )

        return ResolvedFill(kind="inherit", hex=None, source="unresolved")

    def _style_fill_ref(
        self, style_el: etree._Element | None
    ) -> tuple[etree._Element, etree._Element | None] | None:
        """Dereference ``a:style/a:fillRef`` into the theme's fill style list."""
        if style_el is None:
            return None
        ref = style_el.find("a:fillRef", NS)
        if ref is None:
            return None
        ph_color = _first_color_child(ref)
        idx_raw = ref.get("idx")
        try:
            idx = int(idx_raw) if idx_raw is not None else 0
        except ValueError:
            return None
        # idx 0 means "no fill"; 1..n index fillStyleLst; 1001+ index bgFillStyleLst.
        if idx == 0:
            return _NO_FILL_ELEMENT, ph_color
        if idx >= 1001:
            styles = self.theme.bg_fill_styles
            offset = idx - 1001
        else:
            styles = self.theme.fill_styles
            offset = idx - 1
        if 0 <= offset < len(styles):
            return styles[offset], ph_color
        return None

    def _materialise_fill(
        self,
        fill_el: etree._Element,
        source: str,
        *,
        ph_color: etree._Element | None,
    ) -> ResolvedFill:
        local = etree.QName(fill_el).localname
        match local:
            case "solidFill":
                return ResolvedFill(
                    kind="solid",
                    hex=self.resolve_color(fill_el, ph_color=ph_color),
                    source=source,
                )
            case "noFill":
                return ResolvedFill(kind="none", hex=None, source=source)
            case "gradFill":
                # Report the first stop: it is the colour a reader sees at the
                # shape's origin, and it is the only defensible single value.
                stop = fill_el.find("a:gsLst/a:gs", NS)
                return ResolvedFill(
                    kind="gradient",
                    hex=self.resolve_color(stop, ph_color=ph_color) if stop is not None else None,
                    source=source,
                )
            case "blipFill":
                return ResolvedFill(kind="picture", hex=None, source=source)
            case "pattFill":
                fg = fill_el.find("a:fgClr", NS)
                return ResolvedFill(
                    kind="pattern",
                    hex=self.resolve_color(fg, ph_color=ph_color) if fg is not None else None,
                    source=source,
                )
            case "grpFill":
                return ResolvedFill(kind="group", hex=None, source=source)
            case _:
                return ResolvedFill(kind="inherit", hex=None, source=source)

    # -- line --------------------------------------------------------------------

    def resolve_line(
        self,
        sp_pr: etree._Element | None,
        *,
        style_el: etree._Element | None = None,
        ph_type: str | None = None,
        ph_idx: int | None = None,
    ) -> ResolvedLine:
        """Resolve a shape's outline through the same chain as its fill."""
        direct = sp_pr.find("a:ln", NS) if sp_pr is not None else None
        if direct is not None and len(direct):
            return self._materialise_line(direct, "shape:spPr/ln", ph_color=None)

        ref_result = self._style_line_ref(style_el)
        if ref_result is not None:
            line_el, ph_color = ref_result
            return self._materialise_line(line_el, "shape:style/lnRef", ph_color=ph_color)

        for label, source in self._inherited_shape_sources(ph_type, ph_idx):
            source_sp_pr = _sp_pr_of(source)
            inherited = source_sp_pr.find("a:ln", NS) if source_sp_pr is not None else None
            if inherited is not None and len(inherited):
                return self._materialise_line(inherited, f"{label}/ln", ph_color=None)

        return ResolvedLine(kind="inherit", hex=None, width_pt=None, source="unresolved")

    def _style_line_ref(
        self, style_el: etree._Element | None
    ) -> tuple[etree._Element, etree._Element | None] | None:
        if style_el is None:
            return None
        ref = style_el.find("a:lnRef", NS)
        if ref is None:
            return None
        ph_color = _first_color_child(ref)
        idx_raw = ref.get("idx")
        try:
            idx = int(idx_raw) if idx_raw is not None else 0
        except ValueError:
            return None
        offset = idx - 1
        if 0 <= offset < len(self.theme.line_styles):
            return self.theme.line_styles[offset], ph_color
        return None

    def _materialise_line(
        self,
        line_el: etree._Element,
        source: str,
        *,
        ph_color: etree._Element | None,
    ) -> ResolvedLine:
        width_pt = emu_to_pt(int(line_el.get("w"))) if line_el.get("w") else None
        if line_el.find("a:noFill", NS) is not None:
            return ResolvedLine(kind="none", hex=None, width_pt=width_pt, source=source)
        solid = line_el.find("a:solidFill", NS)
        if solid is not None:
            return ResolvedLine(
                kind="solid",
                hex=self.resolve_color(solid, ph_color=ph_color),
                width_pt=width_pt,
                source=source,
            )
        if line_el.find("a:gradFill", NS) is not None:
            return ResolvedLine(kind="gradient", hex=None, width_pt=width_pt, source=source)
        if line_el.find("a:pattFill", NS) is not None:
            return ResolvedLine(kind="pattern", hex=None, width_pt=width_pt, source=source)
        return ResolvedLine(kind="inherit", hex=None, width_pt=width_pt, source=source)

    def _inherited_shape_sources(
        self, ph_type: str | None, ph_idx: int | None
    ) -> list[tuple[str, etree._Element | None]]:
        if ph_type is None and ph_idx is None:
            return []
        return [
            ("layout:placeholder", self.layout_placeholder(ph_type, ph_idx)),
            ("master:placeholder", self.master_placeholder(ph_type)),
        ]

    # -- background --------------------------------------------------------------

    def resolve_background(self, slide_el: etree._Element | None) -> ResolvedFill:
        """Resolve the slide background through slide -> layout -> master.

        Needed to decide whether a logo sits on a dark or light ground, which is
        how the interview distinguishes a primary mark from its reversed variant.
        """
        for label, root in (
            ("slide", slide_el),
            ("layout", self.layout_el),
            ("master", self.master_el),
        ):
            if root is None:
                continue
            bg = root.find("p:cSld/p:bg", NS)
            if bg is None:
                continue
            bg_pr = bg.find("p:bgPr", NS)
            if bg_pr is not None:
                fill = _read_fill(bg_pr)
                if fill is not None:
                    return self._materialise_fill(fill, f"{label}:bgPr", ph_color=None)
            bg_ref = bg.find("p:bgRef", NS)
            if bg_ref is not None:
                ph_color = _first_color_child(bg_ref)
                idx_raw = bg_ref.get("idx")
                try:
                    idx = int(idx_raw) if idx_raw is not None else 0
                except ValueError:
                    continue
                styles = (
                    self.theme.bg_fill_styles if idx >= 1001 else self.theme.fill_styles
                )
                offset = idx - 1001 if idx >= 1001 else idx - 1
                if 0 <= offset < len(styles):
                    return self._materialise_fill(
                        styles[offset], f"{label}:bgRef", ph_color=ph_color
                    )
        return ResolvedFill(kind="inherit", hex=None, source="unresolved")


# --------------------------------------------------------------------------------------
# Element helpers
# --------------------------------------------------------------------------------------


#: A standalone ``a:noFill`` used when ``a:fillRef@idx`` is 0, which means the
#: referencing shape has no fill at all.
_NO_FILL_ELEMENT: Final[etree._Element] = etree.Element(qn("a:noFill"))


def _read_fill(parent: etree._Element | None) -> etree._Element | None:
    """The fill element directly under a ``p:spPr``/``p:bgPr``, if present."""
    if parent is None:
        return None
    for tag in ("solidFill", "noFill", "gradFill", "blipFill", "pattFill", "grpFill"):
        node = parent.find(f"a:{tag}", NS)
        if node is not None:
            return node
    return None


def _def_rpr(ppr: etree._Element | None) -> etree._Element | None:
    if ppr is None:
        return None
    return ppr.find("a:defRPr", NS)


def _level_def_rpr(
    list_style: etree._Element | None, level: int
) -> etree._Element | None:
    """``a:lstStyle/a:lvl<n>pPr/a:defRPr`` for a zero-based level."""
    if list_style is None:
        return None
    node = list_style.find(f"a:lvl{level + 1}pPr/a:defRPr", NS)
    if node is not None:
        return node
    # Levels beyond those defined fall back to the deepest defined level, which
    # is what PowerPoint renders.
    for candidate in range(level, -1, -1):
        node = list_style.find(f"a:lvl{candidate + 1}pPr/a:defRPr", NS)
        if node is not None:
            return node
    return None


def _list_style_of(shape: etree._Element | None) -> etree._Element | None:
    if shape is None:
        return None
    return shape.find("p:txBody/a:lstStyle", NS)


def _first_para_ppr(shape: etree._Element | None) -> etree._Element | None:
    if shape is None:
        return None
    return shape.find("p:txBody/a:p/a:pPr", NS)


def _sp_pr_of(shape: etree._Element | None) -> etree._Element | None:
    if shape is None:
        return None
    return shape.find("p:spPr", NS)


def _style_of(shape: etree._Element | None) -> etree._Element | None:
    if shape is None:
        return None
    return shape.find("p:style", NS)


def _latin_typeface(rpr: etree._Element) -> str | None:
    node = rpr.find("a:latin", NS)
    if node is None:
        return None
    face = node.get("typeface")
    return face or None


def _find_placeholder(
    root: etree._Element | None, ph_type: str | None, ph_idx: int | None
) -> etree._Element | None:
    """Find a placeholder shape in a layout or master shape tree.

    Matching is by ``idx`` first, then by ``type``. PowerPoint itself matches on
    ``idx`` when present, which is why two body placeholders on a two-column
    layout inherit different geometry.
    """
    if root is None:
        return None
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        return None

    candidates: list[tuple[etree._Element, str | None, int | None]] = []
    for shape in tree.iter(qn("p:sp")):
        ph = shape.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is None:
            continue
        idx_raw = ph.get("idx")
        try:
            idx = int(idx_raw) if idx_raw is not None else None
        except ValueError:
            idx = None
        # An absent type attribute means "body" in OOXML.
        candidates.append((shape, ph.get("type") or "body", idx))

    if ph_idx is not None:
        for shape, _, idx in candidates:
            if idx == ph_idx:
                return shape
    if ph_type is not None:
        wanted = {ph_type}
        if ph_type in TITLE_PH_TYPES:
            wanted |= TITLE_PH_TYPES
        for shape, found_type, _ in candidates:
            if found_type in wanted:
                return shape
    # A title placeholder on the slide with neither idx nor a matching type still
    # inherits from the layout title, whose idx is conventionally absent.
    if ph_idx is None and ph_type is None:
        for shape, _, idx in candidates:
            if idx is None:
                return shape
    return None


def _apply_transforms(base: colour.Rgb, node: etree._Element) -> colour.Rgb:
    """Apply DrawingML colour transforms in document order.

    Order matters: ``lumMod`` then ``lumOff`` is a theme "lighter" variant, while
    ``lumOff`` then ``lumMod`` is a different colour entirely.
    """
    result = base
    for child in node:
        local = etree.QName(child).localname
        if local not in _TRANSFORM_TAGS:
            continue
        raw = child.get("val")
        if raw is None:
            continue
        try:
            # DrawingML percentages are thousandths of a percent.
            amount = int(raw) / 100000.0
        except ValueError:
            if raw.endswith("%"):
                try:
                    amount = float(raw[:-1]) / 100.0
                except ValueError:
                    continue
            else:
                continue
        result = colour.apply_transform(result, local, amount)
    return result


def _hsl_attr_to_rgb(node: etree._Element) -> colour.Rgb | None:
    """``a:hslClr`` carries hue in 60000ths of a degree and sat/lum in thousandths
    of a percent."""
    try:
        hue = int(node.get("hue") or 0) / 21600000.0
        sat = int(node.get("sat") or 0) / 100000.0
        lum = int(node.get("lum") or 0) / 100000.0
    except ValueError:
        return None
    red, green, blue = colorsys.hls_to_rgb(hue, lum, sat)
    return colour.Rgb(
        round(max(0.0, min(1.0, red)) * 255),
        round(max(0.0, min(1.0, green)) * 255),
        round(max(0.0, min(1.0, blue)) * 255),
    )


def _scrgb_attr_to_rgb(node: etree._Element) -> colour.Rgb | None:
    """``a:scrgbClr`` carries linear RGB percentages in thousandths of a percent."""
    try:
        channels = [
            int(node.get(key) or 0) / 100000.0 for key in ("r", "g", "b")
        ]
    except ValueError:
        return None
    srgb = [colour.linear_to_srgb(max(0.0, min(1.0, c))) for c in channels]
    return colour.Rgb(
        round(max(0.0, min(1.0, srgb[0])) * 255),
        round(max(0.0, min(1.0, srgb[1])) * 255),
        round(max(0.0, min(1.0, srgb[2])) * 255),
    )


#: ``a:sysClr`` values that appear in real decks, used only when ``lastClr`` is
#: absent, which happens in files written by non-Microsoft tooling.
_SYS_CLR_FALLBACK: Final[dict[str, colour.Rgb]] = {
    "windowText": colour.Rgb(0, 0, 0),
    "window": colour.Rgb(255, 255, 255),
    "captionText": colour.Rgb(0, 0, 0),
    "highlight": colour.Rgb(0, 120, 215),
    "highlightText": colour.Rgb(255, 255, 255),
    "btnFace": colour.Rgb(240, 240, 240),
    "btnText": colour.Rgb(0, 0, 0),
    "grayText": colour.Rgb(109, 109, 109),
    "menuText": colour.Rgb(0, 0, 0),
    "infoText": colour.Rgb(0, 0, 0),
}

#: The DrawingML preset colour names TieOut resolves. The full ECMA-376 list runs
#: to 140 entries; these are the ones that occur in decks in practice.
_PRESET_COLORS: Final[dict[str, colour.Rgb]] = {
    "black": colour.Rgb(0, 0, 0),
    "white": colour.Rgb(255, 255, 255),
    "red": colour.Rgb(255, 0, 0),
    "green": colour.Rgb(0, 128, 0),
    "blue": colour.Rgb(0, 0, 255),
    "yellow": colour.Rgb(255, 255, 0),
    "cyan": colour.Rgb(0, 255, 255),
    "magenta": colour.Rgb(255, 0, 255),
    "gray": colour.Rgb(128, 128, 128),
    "grey": colour.Rgb(128, 128, 128),
    "darkGray": colour.Rgb(169, 169, 169),
    "lightGray": colour.Rgb(211, 211, 211),
    "darkBlue": colour.Rgb(0, 0, 139),
    "darkRed": colour.Rgb(139, 0, 0),
    "darkGreen": colour.Rgb(0, 100, 0),
    "orange": colour.Rgb(255, 165, 0),
    "purple": colour.Rgb(128, 0, 128),
    "navy": colour.Rgb(0, 0, 128),
    "teal": colour.Rgb(0, 128, 128),
    "silver": colour.Rgb(192, 192, 192),
    "maroon": colour.Rgb(128, 0, 0),
    "olive": colour.Rgb(128, 128, 0),
    "lime": colour.Rgb(0, 255, 0),
    "aqua": colour.Rgb(0, 255, 255),
    "fuchsia": colour.Rgb(255, 0, 255),
}
