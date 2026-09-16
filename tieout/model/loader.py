"""``.pptx`` -> :class:`DeckModel`.

This is the only module in TieOut that imports ``python-pptx``. Everything above
it reads the resolved model. Keeping the dependency in one file means a
``python-pptx`` upgrade that changes an API breaks one module rather than
twenty-seven rules, and it makes the "never read an unresolved property" rule
mechanically checkable.

Responsibilities:

* walk the shape tree, recursing into groups and applying their child transforms
  so every reported coordinate is in true slide space;
* resolve every run's font, and every shape's fill and line, through
  :mod:`tieout.model.inherit`;
* attach package-level facts (image hashes, native pixel sizes, notes, hidden
  flags) from :mod:`tieout.model.package`;
* classify archetypes.
"""

from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path
from typing import Any, Final

from lxml import etree
from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from tieout.model import archetype as archetype_module
from tieout.model.deck import (
    ChartModel,
    ChartSeries,
    DeckModel,
    ShapeModel,
    ShapeRef,
    SlideModel,
    TableCell,
    TableModel,
    TextParagraph,
    TextRun,
)
from tieout.model.inherit import (
    NS,
    ColorMap,
    ResolvedFill,
    ResolvedFont,
    SlideContext,
    Theme,
    qn,
)
from tieout.model.package import PackageInfo, load_package
from tieout.model.units import emu_to_pt, ooxml_angle_to_degrees

#: Default text-frame insets in points, as PowerPoint applies them when ``a:bodyPr``
#: omits them. 0.1 inch left/right, 0.05 inch top/bottom.
_DEFAULT_INSET_LR_PT: Final[float] = 7.2
_DEFAULT_INSET_TB_PT: Final[float] = 3.6

_ALIGNMENT_MAP: Final[dict[str, str]] = {
    "l": "left",
    "r": "right",
    "ctr": "centre",
    "just": "justify",
    "justLow": "justify",
    "dist": "distribute",
    "thaiDist": "distribute",
}

_AUTOFIT_MAP: Final[dict[str, str]] = {
    "noAutofit": "none",
    "normAutofit": "shrink_text",
    "spAutoFit": "resize_shape",
}


class DeckLoadError(RuntimeError):
    """Raised when a file cannot be read as a PowerPoint package."""


def load_deck(path: str | Path, *, classify: bool = True) -> DeckModel:
    """Load a .pptx into a fully resolved :class:`DeckModel`."""
    path = Path(path)
    if not path.exists():
        raise DeckLoadError(f"no such file: {path}")

    # Wrapped, like the Presentation() call below. DeckLoadError exists so a
    # caller has one thing to catch; a truncated or non-zip file reaching this
    # unwrapped meant `tieout check` printed a traceback instead of a sentence,
    # and the local UI answered 500 to a mistaken drag-and-drop.
    try:
        package = load_package(path)
    except DeckLoadError:
        raise
    except Exception as exc:
        raise DeckLoadError(
            f"cannot read {path.name} as a PowerPoint package: {exc}"
        ) from exc

    try:
        presentation = Presentation(str(path))
    except Exception as exc:
        raise DeckLoadError(f"cannot open {path.name} as a PowerPoint package: {exc}") from exc

    width_pt = emu_to_pt(presentation.slide_width) or 0.0
    height_pt = emu_to_pt(presentation.slide_height) or 0.0
    default_text_style = _default_text_style(presentation)

    slides: list[SlideModel] = []
    layout_types: dict[str, str] = {}
    hidden: list[int] = []

    for position, slide in enumerate(presentation.slides, start=1):
        context = _build_context(slide, default_text_style)
        layout = slide.slide_layout
        layout_name = _safe_name(layout)
        layout_type = layout.element.get("type") or ""
        if layout_name:
            layout_types[layout_name] = layout_type

        shapes = _load_shape_tree(
            slide.shapes,
            context=context,
            package=package,
            slide_index=position,
            group_path=(),
        )
        is_hidden = slide.element.get("show") == "0"
        if is_hidden:
            hidden.append(position)

        slides.append(
            SlideModel(
                index=position,
                shapes=tuple(shapes),
                width_pt=width_pt,
                height_pt=height_pt,
                layout_name=layout_name,
                layout_type=layout_type or None,
                master_name=_safe_name(layout.slide_master),
                notes_text=_notes_text(slide),
                is_hidden=is_hidden,
                background=context.resolve_background(slide.element),
                part_name=_part_name(slide),
            )
        )

    deck = DeckModel(
        path=path,
        slides=tuple(slides),
        width_pt=width_pt,
        height_pt=height_pt,
        package=package,
        theme_major_font=_deck_theme(presentation).major_latin,
        theme_minor_font=_deck_theme(presentation).minor_latin,
        hidden_slide_indices=tuple(hidden),
        layout_types=layout_types,
    )

    if classify:
        archetype_module.classify_deck(deck)
    return deck


# --------------------------------------------------------------------------------------
# Context construction
# --------------------------------------------------------------------------------------


def _build_context(slide: Any, default_text_style: etree._Element | None) -> SlideContext:
    layout = slide.slide_layout
    master = layout.slide_master
    theme = _theme_for(master)
    return SlideContext(
        theme=theme,
        color_map=ColorMap.parse(master.element),
        layout_el=layout.element,
        master_el=master.element,
        default_text_style=default_text_style,
    )


_THEME_CACHE: dict[int, Theme] = {}


def _theme_for(master: Any) -> Theme:
    """Parse and cache the theme part related to a slide master.

    Cached by part identity: a 26-slide deck has one master and one theme, and
    re-parsing the theme per slide is measurable on large decks.
    """
    key = id(master.part)
    cached = _THEME_CACHE.get(key)
    if cached is not None:
        return cached
    theme = Theme.empty()
    with contextlib.suppress(KeyError, AttributeError, etree.XMLSyntaxError, ValueError):
        theme_part = master.part.part_related_by(RT.THEME)
        theme = Theme.parse(theme_part.blob)
    _THEME_CACHE[key] = theme
    return theme


def _deck_theme(presentation: Any) -> Theme:
    try:
        return _theme_for(presentation.slide_masters[0])
    except (IndexError, AttributeError):
        return Theme.empty()


def _default_text_style(presentation: Any) -> etree._Element | None:
    """``p:defaultTextStyle`` from presentation.xml.

    Plain text boxes inherit from here, not from the master's text styles.
    """
    with contextlib.suppress(AttributeError):
        node = presentation.part.element.find("p:defaultTextStyle", NS)
        if node is not None:
            return node
    return None


def _safe_name(part_like: Any) -> str | None:
    with contextlib.suppress(AttributeError, KeyError):
        name = part_like.name
        if isinstance(name, str) and name:
            return name
    with contextlib.suppress(AttributeError, KeyError):
        node = part_like.element.find("p:cSld", NS)
        if node is not None:
            name = node.get("name")
            return str(name) if name is not None else None
    return None


def _part_name(slide: Any) -> str | None:
    with contextlib.suppress(AttributeError):
        return str(slide.part.partname)
    return None


def _notes_text(slide: Any) -> str:
    """Speaker notes text, empty string when there is no notes slide.

    ``has_notes_slide`` is checked first because accessing ``notes_slide``
    creates one as a side effect, which would make HY-002 report notes on a deck
    that has none.
    """
    with contextlib.suppress(AttributeError, KeyError):
        if not slide.has_notes_slide:
            return ""
        frame = slide.notes_slide.notes_text_frame
        if frame is None:
            return ""
        return str(frame.text or "").strip()
    return ""


# --------------------------------------------------------------------------------------
# Shape tree
# --------------------------------------------------------------------------------------


def _load_shape_tree(
    shapes: Any,
    *,
    context: SlideContext,
    package: PackageInfo,
    slide_index: int,
    group_path: tuple[str, ...],
    transform: _Transform | None = None,
) -> list[ShapeModel]:
    out: list[ShapeModel] = []
    for z_order, shape in enumerate(shapes):
        model = _load_shape(
            shape,
            context=context,
            package=package,
            slide_index=slide_index,
            group_path=group_path,
            z_order=z_order,
            transform=transform,
        )
        if model is not None:
            out.append(model)
    return out


def _load_shape(
    shape: Any,
    *,
    context: SlideContext,
    package: PackageInfo,
    slide_index: int,
    group_path: tuple[str, ...],
    z_order: int,
    transform: _Transform | None,
) -> ShapeModel | None:
    element = shape._element
    name = _shape_name(shape, element)
    shape_id = _shape_id(shape, element)
    ref = ShapeRef(
        slide_index=slide_index,
        shape_id=shape_id,
        name=name,
        group_path=group_path,
    )

    ph_type, ph_idx = _placeholder_identity(element)
    left, top, width, height = _placeholder_aware_geometry(
        element, transform, context=context, ph_type=ph_type, ph_idx=ph_idx
    )
    kind = _shape_kind(shape, element)

    if kind == "group":
        child_transform = _compose_group_transform(element, transform)
        children = _load_shape_tree(
            shape.shapes,
            context=context,
            package=package,
            slide_index=slide_index,
            group_path=(*group_path, name),
            transform=child_transform,
        )
        return ShapeModel(
            ref=ref,
            kind="group",
            left_pt=left,
            top_pt=top,
            width_pt=width,
            height_pt=height,
            rotation=ooxml_angle_to_degrees(_xfrm_attr(element, "rot")),
            is_placeholder=ph_type is not None or ph_idx is not None,
            placeholder_type=ph_type,
            placeholder_idx=ph_idx,
            children=tuple(children),
            z_order=z_order,
            raw_element=element,
        )

    sp_pr = element.find("p:spPr", NS)
    style_el = element.find("p:style", NS)
    body_pr = element.find(".//a:bodyPr", NS)
    autofit, font_scale = _autofit(body_pr)

    paragraphs = _load_paragraphs(
        element,
        context=context,
        ph_type=ph_type,
        ph_idx=ph_idx,
        is_text_box=_is_text_box(body_pr, element),
        font_scale=font_scale,
    )

    table = _load_table(
        element, context=context, ph_type=ph_type, ph_idx=ph_idx, font_scale=font_scale
    )
    chart = _load_chart(shape, context=context) if kind == "chart" else None
    diagram_text = (
        _load_diagram_text(shape, element, package) if kind == "smartart" else ()
    )

    image_sha1, image_part, px_w, px_h = _image_identity(shape, package)

    effective_font = _shape_effective_font(paragraphs, table)
    if effective_font is None and (paragraphs or table is not None):
        effective_font = context.resolve_font(
            run_rpr=None,
            para_ppr=None,
            level=0,
            shape_list_style=element.find("p:txBody/a:lstStyle", NS),
            ph_type=ph_type,
            ph_idx=ph_idx,
            is_text_box=_is_text_box(body_pr, element),
        )

    insets = _insets(body_pr)
    return ShapeModel(
        ref=ref,
        kind=kind,
        left_pt=left,
        top_pt=top,
        width_pt=width,
        height_pt=height,
        rotation=ooxml_angle_to_degrees(_xfrm_attr(element, "rot")),
        flip_horizontal=_xfrm_flag(element, "flipH"),
        flip_vertical=_xfrm_flag(element, "flipV"),
        effective_font=effective_font,
        effective_fill=context.resolve_fill(
            sp_pr, style_el=style_el, ph_type=ph_type, ph_idx=ph_idx
        )
        if kind != "picture"
        else ResolvedFill(kind="picture", hex=None, source="picture"),
        effective_line=context.resolve_line(
            sp_pr, style_el=style_el, ph_type=ph_type, ph_idx=ph_idx
        ),
        is_placeholder=ph_type is not None or ph_idx is not None,
        placeholder_type=ph_type,
        placeholder_idx=ph_idx,
        text_frame_paragraphs=paragraphs,
        has_text_frame=element.find("p:txBody", NS) is not None,
        word_wrap=_word_wrap(body_pr),
        autofit=autofit,
        inset_left_pt=insets[0],
        inset_right_pt=insets[1],
        inset_top_pt=insets[2],
        inset_bottom_pt=insets[3],
        vertical_anchor=body_pr.get("anchor") if body_pr is not None else None,
        text_direction=body_pr.get("vert") if body_pr is not None else None,
        image_sha1=image_sha1,
        image_part_name=image_part,
        image_pixel_width=px_w,
        image_pixel_height=px_h,
        diagram_text=diagram_text,
        table=table,
        chart=chart,
        z_order=z_order,
        layout_geometry_pt=_layout_geometry(context, ph_type, ph_idx),
        raw_element=element,
    )


# --------------------------------------------------------------------------------------
# Group transforms
# --------------------------------------------------------------------------------------


class _Transform:
    """A group's child-space to slide-space mapping.

    A group declares its own position and size (``a:off``/``a:ext``) alongside the
    coordinate space its children are authored in (``a:chOff``/``a:chExt``). Child
    coordinates must be offset and scaled, or every shape inside a group is
    reported at the wrong place -- and groups are how bankers build the callout
    boxes and bridge charts that most often drift.

    Transforms compose, because groups nest.
    """

    __slots__ = ("offset_x", "offset_y", "scale_x", "scale_y")

    def __init__(
        self,
        offset_x: float,
        offset_y: float,
        scale_x: float,
        scale_y: float,
    ) -> None:
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.scale_x = scale_x
        self.scale_y = scale_y

    def apply_point(self, x: float, y: float) -> tuple[float, float]:
        return (self.offset_x + x * self.scale_x, self.offset_y + y * self.scale_y)

    def apply_size(self, width: float, height: float) -> tuple[float, float]:
        return (width * self.scale_x, height * self.scale_y)

    def compose(self, inner: _Transform) -> _Transform:
        """Apply ``self`` after ``inner``, for a group inside a group."""
        return _Transform(
            offset_x=self.offset_x + inner.offset_x * self.scale_x,
            offset_y=self.offset_y + inner.offset_y * self.scale_y,
            scale_x=self.scale_x * inner.scale_x,
            scale_y=self.scale_y * inner.scale_y,
        )

    @staticmethod
    def identity() -> _Transform:
        return _Transform(0.0, 0.0, 1.0, 1.0)


def _compose_group_transform(
    element: etree._Element, outer: _Transform | None
) -> _Transform:
    xfrm = element.find("p:grpSpPr/a:xfrm", NS)
    if xfrm is None:
        return outer or _Transform.identity()

    off = xfrm.find("a:off", NS)
    ext = xfrm.find("a:ext", NS)
    ch_off = xfrm.find("a:chOff", NS)
    ch_ext = xfrm.find("a:chExt", NS)

    off_x = _int_attr(off, "x")
    off_y = _int_attr(off, "y")
    ext_cx = _int_attr(ext, "cx")
    ext_cy = _int_attr(ext, "cy")
    ch_off_x = _int_attr(ch_off, "x")
    ch_off_y = _int_attr(ch_off, "y")
    ch_ext_cx = _int_attr(ch_ext, "cx")
    ch_ext_cy = _int_attr(ch_ext, "cy")

    scale_x = (ext_cx / ch_ext_cx) if ch_ext_cx else 1.0
    scale_y = (ext_cy / ch_ext_cy) if ch_ext_cy else 1.0

    # A child at chOff maps to the group's off, then scales outward from there.
    inner = _Transform(
        offset_x=(emu_to_pt(off_x) or 0.0) - (emu_to_pt(ch_off_x) or 0.0) * scale_x,
        offset_y=(emu_to_pt(off_y) or 0.0) - (emu_to_pt(ch_off_y) or 0.0) * scale_y,
        scale_x=scale_x,
        scale_y=scale_y,
    )
    return outer.compose(inner) if outer else inner


def _find_xfrm(element: etree._Element) -> etree._Element | None:
    """The transform element that belongs to this shape, not to a descendant.

    Each shape kind stores it somewhere different, and a ``graphicFrame`` uses
    ``p:xfrm`` in the presentation namespace rather than ``a:xfrm``. Searching
    with a blanket ``.//a:xfrm`` finds a nested table's transform on a table
    frame and nothing at all on a chart frame, which reports every table and
    chart on the deck at the slide origin.
    """
    for path in ("p:spPr/a:xfrm", "p:xfrm", "p:grpSpPr/a:xfrm"):
        node = element.find(path, NS)
        if node is not None:
            return node
    return None


def _geometry(
    element: etree._Element, transform: _Transform | None
) -> tuple[float, float, float, float]:
    """Shape geometry in true slide space, in points."""
    xfrm = _find_xfrm(element)
    if xfrm is None:
        return (0.0, 0.0, 0.0, 0.0)
    off = xfrm.find("a:off", NS)
    ext = xfrm.find("a:ext", NS)
    left = emu_to_pt(_int_attr(off, "x")) or 0.0
    top = emu_to_pt(_int_attr(off, "y")) or 0.0
    width = emu_to_pt(_int_attr(ext, "cx")) or 0.0
    height = emu_to_pt(_int_attr(ext, "cy")) or 0.0
    if transform is not None:
        left, top = transform.apply_point(left, top)
        width, height = transform.apply_size(width, height)
    return (left, top, width, height)


def _placeholder_aware_geometry(
    element: etree._Element,
    transform: _Transform | None,
    *,
    context: SlideContext,
    ph_type: str | None,
    ph_idx: int | None,
) -> tuple[float, float, float, float]:
    """Geometry, falling back up the placeholder chain when the shape omits it.

    This is not an edge case. A placeholder the author never dragged has no
    ``a:xfrm`` at all, and that is the normal state of a title on a
    house-template slide. Reading it as (0, 0, 0, 0) puts every untouched title
    at the slide origin, which makes LO-002 fire on the entire deck and teaches
    the learning engine that titles live in the top-left corner.
    """
    if _find_xfrm(element) is not None:
        return _geometry(element, transform)
    if ph_type is None and ph_idx is None:
        return _geometry(element, transform)

    for source in (
        context.layout_placeholder(ph_type, ph_idx),
        context.master_placeholder(ph_type),
    ):
        if source is None:
            continue
        inherited = _geometry(source, transform)
        if inherited != (0.0, 0.0, 0.0, 0.0):
            return inherited
    return _geometry(element, transform)


def _int_attr(element: etree._Element | None, name: str) -> int:
    if element is None:
        return 0
    raw = element.get(name)
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _xfrm_attr(element: etree._Element, name: str) -> str | None:
    xfrm = _find_xfrm(element)
    return xfrm.get(name) if xfrm is not None else None


def _xfrm_flag(element: etree._Element, name: str) -> bool:
    return _xfrm_attr(element, name) in ("1", "true")


# --------------------------------------------------------------------------------------
# Shape identity
# --------------------------------------------------------------------------------------


def _shape_name(shape: Any, element: etree._Element) -> str:
    with contextlib.suppress(AttributeError):
        name = shape.name
        if isinstance(name, str) and name:
            return name
    for path in ("p:nvSpPr/p:cNvPr", "p:nvPicPr/p:cNvPr", "p:nvGrpSpPr/p:cNvPr",
                 "p:nvGraphicFramePr/p:cNvPr", "p:nvCxnSpPr/p:cNvPr"):
        node = element.find(path, NS)
        if node is not None and node.get("name"):
            return str(node.get("name"))
    return "<unnamed>"


def _shape_id(shape: Any, element: etree._Element) -> int:
    with contextlib.suppress(AttributeError, TypeError, ValueError):
        return int(shape.shape_id)
    for path in ("p:nvSpPr/p:cNvPr", "p:nvPicPr/p:cNvPr", "p:nvGrpSpPr/p:cNvPr",
                 "p:nvGraphicFramePr/p:cNvPr", "p:nvCxnSpPr/p:cNvPr"):
        node = element.find(path, NS)
        if node is not None and node.get("id"):
            with contextlib.suppress(ValueError):
                return int(str(node.get("id")))
    return 0


def _placeholder_identity(element: etree._Element) -> tuple[str | None, int | None]:
    ph = element.find(".//p:nvPr/p:ph", NS)
    if ph is None:
        return (None, None)
    # An absent type attribute means "body" per ECMA-376.
    ph_type = ph.get("type") or "body"
    idx_raw = ph.get("idx")
    idx: int | None = None
    if idx_raw is not None:
        with contextlib.suppress(ValueError):
            idx = int(idx_raw)
    return (ph_type, idx)


def _shape_kind(shape: Any, element: etree._Element) -> str:
    tag = etree.QName(element).localname
    if tag == "grpSp":
        return "group"
    if tag == "pic":
        return "picture"
    if tag == "cxnSp":
        return "connector"
    if tag == "graphicFrame":
        uri = element.find(".//a:graphicData", NS)
        graphic_uri = uri.get("uri", "") if uri is not None else ""
        if "table" in graphic_uri:
            return "table"
        if "chart" in graphic_uri:
            return "chart"
        if "diagram" in graphic_uri:
            return "smartart"
        if "ole" in graphic_uri:
            return "ole"
        return "unknown"
    if tag == "sp":
        if element.find(".//p:nvPr/p:ph", NS) is not None:
            return "placeholder"
        body_pr = element.find(".//p:nvSpPr/p:cNvSpPr", NS)
        if body_pr is not None and body_pr.get("txBox") == "1":
            return "textbox"
        geometry = element.find("p:spPr/a:custGeom", NS)
        if geometry is not None:
            return "freeform"
        return "autoshape"
    with contextlib.suppress(AttributeError):
        return str(shape.shape_type).lower()
    return "unknown"


def _is_text_box(body_pr: etree._Element | None, element: etree._Element) -> bool:
    node = element.find(".//p:nvSpPr/p:cNvSpPr", NS)
    return node is not None and node.get("txBox") == "1"


# --------------------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------------------


def _load_paragraphs(
    element: etree._Element,
    *,
    context: SlideContext,
    ph_type: str | None,
    ph_idx: int | None,
    is_text_box: bool,
    font_scale: float | None,
) -> tuple[TextParagraph, ...]:
    body = element.find("p:txBody", NS)
    if body is None:
        return ()
    list_style = body.find("a:lstStyle", NS)
    return _paragraphs_from_body(
        body,
        context=context,
        list_style=list_style,
        ph_type=ph_type,
        ph_idx=ph_idx,
        is_text_box=is_text_box,
        font_scale=font_scale,
    )


def _paragraphs_from_body(
    body: etree._Element,
    *,
    context: SlideContext,
    list_style: etree._Element | None,
    ph_type: str | None,
    ph_idx: int | None,
    is_text_box: bool,
    font_scale: float | None,
) -> tuple[TextParagraph, ...]:
    out: list[TextParagraph] = []
    for para in body.findall("a:p", NS):
        ppr = para.find("a:pPr", NS)
        level = 0
        if ppr is not None and ppr.get("lvl"):
            with contextlib.suppress(ValueError):
                level = int(str(ppr.get("lvl")))

        runs: list[TextRun] = []
        for node in para:
            local = etree.QName(node).localname
            if local == "r":
                text = node.findtext("a:t", default="", namespaces=NS)
                runs.append(
                    TextRun(
                        text=text,
                        font=context.resolve_font(
                            run_rpr=node.find("a:rPr", NS),
                            para_ppr=ppr,
                            level=level,
                            shape_list_style=list_style,
                            ph_type=ph_type,
                            ph_idx=ph_idx,
                            is_text_box=is_text_box,
                            body_pr_normalise=font_scale,
                        ),
                        hyperlink=_hyperlink(node),
                    )
                )
            elif local == "br":
                # A soft line break renders as a newline and must not be dropped:
                # TY-002's double-space detection depends on exact text.
                runs.append(
                    TextRun(
                        text="\n",
                        font=context.resolve_font(
                            run_rpr=None,
                            para_ppr=ppr,
                            level=level,
                            shape_list_style=list_style,
                            ph_type=ph_type,
                            ph_idx=ph_idx,
                            is_text_box=is_text_box,
                            body_pr_normalise=font_scale,
                        ),
                    )
                )
            elif local == "fld":
                # A field (slide number, date) carries rendered text in a:t.
                text = node.findtext("a:t", default="", namespaces=NS)
                runs.append(
                    TextRun(
                        text=text,
                        font=context.resolve_font(
                            run_rpr=node.find("a:rPr", NS),
                            para_ppr=ppr,
                            level=level,
                            shape_list_style=list_style,
                            ph_type=ph_type,
                            ph_idx=ph_idx,
                            is_text_box=is_text_box,
                            body_pr_normalise=font_scale,
                        ),
                    )
                )

        bullet_kind, bullet_char, autonum = _bullet(ppr)
        out.append(
            TextParagraph(
                runs=tuple(runs),
                level=level,
                alignment=_alignment(ppr),
                bullet_kind=bullet_kind,
                bullet_char=bullet_char,
                bullet_autonum_type=autonum,
                space_before_pt=_spacing(ppr, "a:spcBef"),
                space_after_pt=_spacing(ppr, "a:spcAft"),
                line_spacing=_line_spacing(ppr),
                indent_pt=emu_to_pt(_opt_int(ppr, "indent")) if ppr is not None else None,
                margin_left_pt=emu_to_pt(_opt_int(ppr, "marL")) if ppr is not None else None,
            )
        )
    return tuple(out)


def _hyperlink(run_node: etree._Element) -> str | None:
    rpr = run_node.find("a:rPr", NS)
    if rpr is None:
        return None
    link = rpr.find("a:hlinkClick", NS)
    if link is None:
        return None
    rel_id = link.get(f"{{{NS['r']}}}id")
    return str(rel_id) if rel_id is not None else None


def _alignment(ppr: etree._Element | None) -> str | None:
    if ppr is None:
        return None
    raw = ppr.get("algn")
    if raw is None:
        return None
    return _ALIGNMENT_MAP.get(raw, str(raw))


def _opt_int(element: etree._Element, name: str) -> int | None:
    raw = element.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _spacing(ppr: etree._Element | None, tag: str) -> float | None:
    if ppr is None:
        return None
    node = ppr.find(f"{tag}/a:spcPts", NS)
    if node is None:
        return None
    raw = node.get("val")
    if raw is None:
        return None
    try:
        return int(raw) / 100.0
    except ValueError:
        return None


def _line_spacing(ppr: etree._Element | None) -> float | None:
    """Line spacing as a multiple when given as a percentage, else in points."""
    if ppr is None:
        return None
    percent = ppr.find("a:lnSpc/a:spcPct", NS)
    if percent is not None and percent.get("val"):
        with contextlib.suppress(ValueError):
            return int(str(percent.get("val"))) / 100000.0
    points = ppr.find("a:lnSpc/a:spcPts", NS)
    if points is not None and points.get("val"):
        with contextlib.suppress(ValueError):
            return int(str(points.get("val"))) / 100.0
    return None


def _bullet(ppr: etree._Element | None) -> tuple[str | None, str | None, str | None]:
    if ppr is None:
        return (None, None, None)
    if ppr.find("a:buNone", NS) is not None:
        return ("none", None, None)
    char = ppr.find("a:buChar", NS)
    if char is not None:
        return ("char", char.get("char"), None)
    autonum = ppr.find("a:buAutoNum", NS)
    if autonum is not None:
        return ("auto", None, autonum.get("type"))
    return (None, None, None)


def _autofit(body_pr: etree._Element | None) -> tuple[str | None, float | None]:
    """The autofit mode and, for shrink-on-overflow, the applied font scale."""
    if body_pr is None:
        return (None, None)
    for tag, label in _AUTOFIT_MAP.items():
        node = body_pr.find(f"a:{tag}", NS)
        if node is None:
            continue
        scale: float | None = None
        if tag == "normAutofit" and node.get("fontScale"):
            with contextlib.suppress(ValueError):
                scale = int(str(node.get("fontScale"))) / 100000.0
        return (label, scale)
    return (None, None)


def _word_wrap(body_pr: etree._Element | None) -> bool | None:
    if body_pr is None:
        return None
    raw = body_pr.get("wrap")
    if raw is None:
        return None
    return bool(raw == "square")


def _insets(body_pr: etree._Element | None) -> tuple[float, float, float, float]:
    """Text-frame insets in points, defaulting to PowerPoint's own values."""
    if body_pr is None:
        return (
            _DEFAULT_INSET_LR_PT,
            _DEFAULT_INSET_LR_PT,
            _DEFAULT_INSET_TB_PT,
            _DEFAULT_INSET_TB_PT,
        )
    def read(name: str, default: float) -> float:
        raw = body_pr.get(name)
        if raw is None:
            return default
        try:
            return emu_to_pt(int(raw)) or 0.0
        except ValueError:
            return default

    return (
        read("lIns", _DEFAULT_INSET_LR_PT),
        read("rIns", _DEFAULT_INSET_LR_PT),
        read("tIns", _DEFAULT_INSET_TB_PT),
        read("bIns", _DEFAULT_INSET_TB_PT),
    )


#: The identity of a resolved font, for character-weighted dominance.
_FontKey = tuple[str | None, float | None, bool | None, bool | None, str | None]


def _shape_effective_font(
    paragraphs: tuple[TextParagraph, ...], table: TableModel | None
) -> ResolvedFont | None:
    """The font covering the most characters in the shape.

    "Effective font" for a shape with mixed formatting is inherently a summary.
    Character-weighted dominance is the honest one: it is what a reader perceives
    as the shape's typeface.
    """
    weights: dict[_FontKey, int] = {}
    fonts: dict[_FontKey, ResolvedFont] = {}
    sources = list(paragraphs)
    if table is not None:
        sources.extend(table.all_paragraphs)
    for paragraph in sources:
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            key = (
                run.font.name,
                run.font.size_pt,
                run.font.bold,
                run.font.italic,
                run.font.color_hex,
            )
            weights[key] = weights.get(key, 0) + len(run.text)
            fonts[key] = run.font
    if not weights:
        return None
    best = max(weights.items(), key=lambda item: item[1])[0]
    return fonts[best]


# --------------------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------------------


def _load_table(
    element: etree._Element,
    *,
    context: SlideContext,
    ph_type: str | None,
    ph_idx: int | None,
    font_scale: float | None,
) -> TableModel | None:
    table_el = element.find(".//a:tbl", NS)
    if table_el is None:
        return None

    properties = table_el.find("a:tblPr", NS)
    rows = table_el.findall("a:tr", NS)
    grid_cols = table_el.findall("a:tblGrid/a:gridCol", NS)

    cells: list[TableCell] = []
    for row_index, row in enumerate(rows):
        for col_index, cell_el in enumerate(row.findall("a:tc", NS)):
            body = cell_el.find("a:txBody", NS)
            paragraphs: tuple[TextParagraph, ...] = ()
            if body is not None:
                paragraphs = _paragraphs_from_body(
                    body,
                    context=context,
                    list_style=body.find("a:lstStyle", NS),
                    ph_type=ph_type,
                    ph_idx=ph_idx,
                    is_text_box=False,
                    font_scale=font_scale,
                )
            row_span = _span(cell_el, "rowSpan")
            col_span = _span(cell_el, "gridSpan")
            continuation = (
                cell_el.get("hMerge") == "1" or cell_el.get("vMerge") == "1"
            )
            cell_pr = cell_el.find("a:tcPr", NS)
            cells.append(
                TableCell(
                    row=row_index,
                    column=col_index,
                    paragraphs=paragraphs,
                    row_span=row_span,
                    column_span=col_span,
                    is_merge_continuation=continuation,
                    fill=context.resolve_fill(cell_pr) if cell_pr is not None else None,
                )
            )

    return TableModel(
        row_count=len(rows),
        column_count=len(grid_cols) or max((c.column + 1 for c in cells), default=0),
        cells=tuple(cells),
        column_widths_pt=tuple(
            emu_to_pt(_int_attr(col, "w")) or 0.0 for col in grid_cols
        ),
        row_heights_pt=tuple(emu_to_pt(_int_attr(row, "h")) or 0.0 for row in rows),
        first_row_is_header=_table_flag(properties, "firstRow"),
        first_column_is_header=_table_flag(properties, "firstCol"),
        banded_rows=_table_flag(properties, "bandRow"),
    )


def _span(cell_el: etree._Element, name: str) -> int:
    raw = cell_el.get(name)
    if raw is None:
        return 1
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _table_flag(properties: etree._Element | None, name: str) -> bool:
    if properties is None:
        return False
    return properties.get(name) in ("1", "true")


# --------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------

_C: Final[str] = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_CHART_NS: Final[dict[str, str]] = {**NS, "c": _C}

_CHART_TYPE_TAGS: Final[tuple[str, ...]] = (
    "barChart",
    "bar3DChart",
    "lineChart",
    "line3DChart",
    "pieChart",
    "pie3DChart",
    "doughnutChart",
    "areaChart",
    "area3DChart",
    "scatterChart",
    "bubbleChart",
    "radarChart",
    "stockChart",
    "surfaceChart",
    "ofPieChart",
)


#: ``dgm`` is not in the model's shared namespace map, which covers only the
#: three the shape tree itself uses.
_DIAGRAM_NS: Final[dict[str, str]] = {
    **NS,
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}


def _load_diagram_text(
    shape: Any, element: etree._Element, package: PackageInfo
) -> tuple[str, ...]:
    """The labels inside a SmartArt graphic, via the relationship that names them.

    A ``dgm`` graphic frame stores no text. It points at a diagram data part
    through ``dgm:relIds/@r:dm``, and that part holds every label, which is why
    a draft marker or a client's name inside SmartArt was invisible to every
    text rule in the tool.

    Only the text. The diagram's internal geometry and colours remain
    unmodelled, so nothing here invites a layout rule to measure a box it
    cannot see.
    """
    rel_ids = element.find(".//dgm:relIds", _DIAGRAM_NS)
    if rel_ids is None:
        return ()
    rel_id = rel_ids.get(f"{{{NS['r']}}}dm")
    if not rel_id:
        return ()

    source = _part_name(shape)
    if source is None:
        return ()
    # python-pptx reports a part name with a leading slash; the package layer
    # keys on the zip entry, which has none.
    source = source.lstrip("/")

    for relationship in package.relationships:
        if relationship.source_part != source or relationship.rel_id != rel_id:
            continue
        target = package.resolve(relationship)
        if target is None:
            return ()
        return package.diagram_text.get(target.lstrip("/"), ())
    return ()


def _load_chart(shape: Any, *, context: SlideContext) -> ChartModel | None:
    """Read a chart part at the depth TieOut can measure without rendering."""
    root: etree._Element | None = None
    with contextlib.suppress(AttributeError, KeyError, ValueError):
        root = shape.chart._chartSpace
    if root is None:
        return None

    chart_type = "unknown"
    for tag in _CHART_TYPE_TAGS:
        if root.find(f".//c:{tag}", _CHART_NS) is not None:
            chart_type = tag.removesuffix("Chart")
            break

    title_el = root.find(".//c:chart/c:title", _CHART_NS)
    auto_deleted = root.find(".//c:chart/c:autoTitleDeleted", _CHART_NS)
    title_deleted = auto_deleted is not None and auto_deleted.get("val") in ("1", "true")
    title_text = _chart_text(title_el) if title_el is not None else None
    has_title = title_el is not None and not title_deleted and bool(title_text)

    series: list[ChartSeries] = []
    for ser in root.findall(".//c:ser", _CHART_NS):
        name_el = ser.find("c:tx", _CHART_NS)
        point_count_el = ser.find(".//c:val//c:ptCount", _CHART_NS)
        count = 0
        if point_count_el is not None and point_count_el.get("val"):
            with contextlib.suppress(ValueError):
                count = int(str(point_count_el.get("val")))
        # Labels are usually set for the whole plot, not per series: PowerPoint
        # writes one `c:dLbls` beside the series rather than inside each. Reading
        # only the series' own block reports every series of a labelled chart as
        # unlabelled, which is exactly the false positive CH-004 must not make.
        labels = ser.find("c:dLbls", _CHART_NS)
        inherited = ser.getparent().find("c:dLbls", _CHART_NS) \
            if ser.getparent() is not None else None
        series.append(
            ChartSeries(
                name=_chart_text(name_el) if name_el is not None else None,
                point_count=count,
                fill_hex=_series_fill(ser, context),
                has_data_labels=_labels_shown(labels, inherited),
                label_number_format=_label_format(labels) or _label_format(inherited),
            )
        )

    # Take the category labels from the first series only. Every series repeats
    # the same category list, so collecting them all reports a five-category
    # chart with two series as having ten categories.
    first_series = root.find(".//c:ser", _CHART_NS)
    categories = tuple(
        node.text or ""
        for node in (
            first_series.findall(".//c:cat//c:pt/c:v", _CHART_NS)
            if first_series is not None
            else []
        )
        if node.text
    )

    axis_titles: list[str] = []
    for axis_tag in ("c:catAx", "c:valAx", "c:dateAx", "c:serAx"):
        for axis in root.findall(f".//{axis_tag}", _CHART_NS):
            axis_title = axis.find("c:title", _CHART_NS)
            if axis_title is not None:
                text = _chart_text(axis_title)
                if text:
                    axis_titles.append(text)

    # A chart's text properties are almost always expressed as ``a:defRPr``
    # inside ``c:txPr`` rather than as ``a:rPr`` on a run, because chart text is
    # generated rather than typed. Collecting only ``a:rPr`` finds nothing at all
    # on a normal chart, and the chart_label font role is then never learned.
    font_sources = [
        *root.findall(".//a:rPr", NS),
        *root.findall(".//a:defRPr", NS),
    ]
    fonts = tuple(
        context.resolve_font(
            run_rpr=rpr,
            para_ppr=None,
            level=0,
            shape_list_style=None,
            ph_type=None,
            ph_idx=None,
            is_text_box=True,
        )
        for rpr in font_sources
    )

    text_strings = _chart_text_strings(root, title_text, series, categories, axis_titles)
    axis_minimum, axis_maximum, has_value_axis = _value_axis(root)

    return ChartModel(
        chart_type=chart_type,
        has_title=has_title,
        title_text=title_text,
        series=tuple(series),
        categories=categories,
        has_data_labels=_chart_shows_values(root),
        has_legend=root.find(".//c:legend", _CHART_NS) is not None,
        axis_titles=tuple(axis_titles),
        fonts=fonts,
        text_strings=text_strings,
        value_axis_minimum=axis_minimum,
        value_axis_maximum=axis_maximum,
        has_value_axis=has_value_axis,
    )


def _labels_shown(
    labels: etree._Element | None, inherited: etree._Element | None = None
) -> bool:
    """Whether this series draws a data label, its own setting or the plot's.

    Present is not the same as shown: PowerPoint writes a ``dLbls`` block with
    every flag off whenever the labels have been turned *off*, so reading the
    element's existence would report labels on a chart that has none. A series
    that says nothing inherits the plot's setting, and a series that switches
    them off overrides it.
    """
    own = _labels_state(labels)
    if own is not None:
        return own
    return _labels_state(inherited) or False


def _labels_state(labels: etree._Element | None) -> bool | None:
    """``True``/``False`` where the block decides, ``None`` where it is silent."""
    if labels is None:
        return None
    deleted = labels.find("c:delete", _CHART_NS)
    if deleted is not None and deleted.get("val") in ("1", "true"):
        return False
    shown = labels.find("c:showVal", _CHART_NS)
    if shown is None:
        return None
    return shown.get("val") in ("1", "true")


def _label_format(labels: etree._Element | None) -> str | None:
    if labels is None:
        return None
    fmt = labels.find("c:numFmt", _CHART_NS)
    code = fmt.get("formatCode") if fmt is not None else None
    return code or None


def _value_axis(root: etree._Element) -> tuple[float | None, float | None, bool]:
    """The value axis' manual bounds, and whether it is drawn.

    Only a *manual* bound is read. An axis left to scale itself has no minimum
    in the XML at all, and inventing one from the data would turn PowerPoint's
    own default into something the author chose.
    """
    axis = root.find(".//c:valAx", _CHART_NS)
    if axis is None:
        return None, None, False

    deleted = axis.find("c:delete", _CHART_NS)
    drawn = not (deleted is not None and deleted.get("val") in ("1", "true"))

    def _bound(tag: str) -> float | None:
        node = axis.find(f"c:scaling/c:{tag}", _CHART_NS)
        if node is None or node.get("val") is None:
            return None
        try:
            return float(str(node.get("val")))
        except ValueError:  # pragma: no cover - malformed chart part
            return None

    return _bound("min"), _bound("max"), drawn


def _series_fill(ser: etree._Element, context: SlideContext) -> str | None:
    """A chart series' own fill colour, resolved to sRGB, where it draws anything.

    Only the series' explicit ``c:spPr`` fill. A series with no fill of its own
    takes its colour from the theme's chart colour cycle, which depends on the
    series index and the chart style and is not modelled here -- it is chosen by
    the template rather than typed by the author, and reporting a colour nobody
    picked is worse than reporting none.

    ``None`` too when every data point overrides it. A pie or doughnut is drawn
    a slice at a time, and a chart whose slices each carry a ``c:dPt`` fill
    paints none of its series colour: the real deck this was written for held a
    doughnut whose three segments were navy, gold and pale blue -- every one of
    them on the palette -- above a series fill of PowerPoint's default accent
    blue that nothing anywhere drew. Reporting that is the same defect as
    measuring a text frame instead of its text.

    Resolved through the slide's own context, so a series painted in a scheme
    colour comes back as the hex that scheme slot actually holds rather than as
    the token.
    """
    fill = ser.find("c:spPr/a:solidFill", _CHART_NS)
    if fill is None:
        return None
    if _every_point_overrides(ser):
        return None
    return context.resolve_color(fill)


def _every_point_overrides(ser: etree._Element) -> bool:
    """Whether each of the series' points carries a fill of its own.

    Counted against the point count the series declares rather than against the
    number of ``c:dPt`` elements alone, because a chart that recolours three of
    its five slices still draws the series colour on the other two.
    """
    overrides: set[str] = set()
    for point in ser.findall("c:dPt", _CHART_NS):
        if point.find("c:spPr/a:solidFill", _CHART_NS) is None:
            continue
        index = point.find("c:idx", _CHART_NS)
        value = index.get("val") if index is not None else None
        if value:
            overrides.add(value)
    if not overrides:
        return False
    counts = [
        int(node.get("val") or 0)
        for node in ser.iterfind("c:val/c:numRef/c:numCache/c:ptCount", _CHART_NS)
    ]
    declared = max(counts, default=0)
    return declared > 0 and len(overrides) >= declared


def _chart_text_strings(
    root: etree._Element,
    title_text: str | None,
    series: list[ChartSeries],
    categories: tuple[str, ...],
    axis_titles: list[str],
) -> tuple[str, ...]:
    """Every human-authored string in the chart part.

    Series names, category labels, the title and axis titles, plus any rich text
    run. Numeric values are excluded: they are data, and scanning them for
    placeholder markers or terminology variants produces only noise.
    """
    parts: list[str] = []
    if title_text:
        parts.append(title_text)
    parts.extend(name for name in (s.name for s in series) if name)
    parts.extend(label for label in categories if label.strip())
    parts.extend(axis_titles)
    parts.extend(
        node.text.strip()
        for node in root.iter(qn("a:t"))
        if node.text and node.text.strip()
    )
    seen: dict[str, None] = {}
    for part in parts:
        seen.setdefault(part, None)
    return tuple(seen)


def _chart_shows_values(root: etree._Element) -> bool:
    """Whether any data-label group on the chart displays values."""
    for node in root.findall(".//c:dLbls/c:showVal", _CHART_NS):
        if node.get("val") in ("1", "true"):
            return True
    return False


def _chart_text(element: etree._Element) -> str | None:
    parts = [node.text for node in element.iter(qn("a:t")) if node.text]
    if not parts:
        parts = [node.text for node in element.iter(f"{{{_C}}}v") if node.text]
    joined = "".join(parts).strip()
    return joined or None


# --------------------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------------------


def _image_identity(
    shape: Any, package: PackageInfo
) -> tuple[str | None, str | None, int | None, int | None]:
    """SHA1, part name and native pixel size of a picture shape.

    The part name and pixel size come from the package layer so they are
    byte-identical to what the logo deriver clusters on, regardless of how many
    relationships happen to point at the same media part.
    """
    with contextlib.suppress(AttributeError, KeyError, ValueError):
        image = shape.image
        sha1 = hashlib.sha1(image.blob, usedforsecurity=False).hexdigest()
        media = package.media_by_sha1(sha1)
        if media is not None:
            return (sha1, media.part_name, media.pixel_width, media.pixel_height)
        width, height = getattr(image, "size", (None, None))
        return (sha1, getattr(image, "filename", None), width, height)
    return (None, None, None, None)


def _layout_geometry(
    context: SlideContext, ph_type: str | None, ph_idx: int | None
) -> tuple[float, float, float, float] | None:
    """The geometry this placeholder is supposed to have, for BR-008's drift check.

    Resolved through the layout then the master, because a layout placeholder
    frequently declares no transform of its own and inherits the master's. Taking
    only the layout would report "no expected geometry" for exactly the untouched
    placeholders whose drift matters most.
    """
    if ph_type is None and ph_idx is None:
        return None
    for source in (
        context.layout_placeholder(ph_type, ph_idx),
        context.master_placeholder(ph_type),
    ):
        if source is None:
            continue
        geometry = _geometry(source, None)
        if geometry != (0.0, 0.0, 0.0, 0.0):
            return geometry
    return None
