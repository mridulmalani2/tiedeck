"""Where the font files are, and what a run of text actually measures.

Lives in the model layer rather than in a rule because two callers need it and
a rule may not import another. LO-006 measures overflow against the real
outlines; :mod:`tieout.model.extent` narrows a text frame to the rectangle its
glyphs occupy, and does it far more tightly when the face is available than the
per-character bound it falls back on.

Nothing here substitutes an arbitrary font. A measurement taken in the wrong
typeface is worse than no measurement, because it looks like a measurement --
so an unavailable face returns ``None`` and the caller decides what to do with
not knowing.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Final

from PIL import ImageFont

#: Metric-compatible substitutions. Liberation Sans is metrically identical to
#: Arial by design and Liberation Serif to Times New Roman, so measuring one and
#: reporting the other is a substitution, not a guess. A typeface neither on this
#: list nor installed under a matching family name is left unmeasured.
_METRIC_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    # Carlito and Caladea are Google's metric-compatible pair for Calibri and
    # Cambria, and ship with LibreOffice. They matter more than the rest here:
    # Calibri and Cambria are what a banking deck is actually set in, and
    # neither can be installed on a build machine for licensing reasons.
    "calibri": ("carlito",),
    "cambria": ("caladea",),
    "arial": ("liberationsans",),
    "helvetica": ("liberationsans",),
    "arial narrow": ("liberationsansnarrow",),
    "times new roman": ("liberationserif",),
    "times": ("liberationserif",),
    "courier new": ("liberationmono",),
    "courier": ("liberationmono",),
}

#: Where installed fonts live. Searched in order; the first family match wins.
_FONT_DIRECTORIES: Final[tuple[str, ...]] = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "C:/Windows/Fonts",
)

#: Fonts are loaded once at this pixel size and measurements scaled to the
#: requested point size. Loading at the point size directly would quantise a
#: 7.5pt run to 7px and lose a twentieth of its width.
_MEASURE_PIXELS: Final[int] = 64


def font_key(name: str) -> str:
    """A family name reduced to letters and digits, for matching across spellings."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def style_key(*, bold: bool, italic: bool) -> str:
    if bold and italic:
        return "bolditalic"
    if bold:
        return "bold"
    if italic:
        return "italic"
    return "regular"


@functools.lru_cache(maxsize=1)
def installed_fonts() -> dict[str, dict[str, str]]:
    """Family key -> style key -> font file path, for every installed TTF and OTF.

    Built once per process from each font's *own* family and style names rather
    than its filename, so an "exact family-name match" means the family the deck
    asked for and not a file that happens to be spelled like it. The traversal is
    sorted and the first match per family and style wins, so two machines with
    the same fonts installed produce the same index -- which determinism
    requires.
    """
    index: dict[str, dict[str, str]] = {}
    for directory in _FONT_DIRECTORIES:
        root = Path(directory)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                family, style = ImageFont.truetype(str(path), 16).getname()
            except OSError:
                continue
            if not family:
                continue
            lowered = (style or "Regular").casefold()
            key = style_key(
                bold="bold" in lowered,
                italic="italic" in lowered or "oblique" in lowered,
            )
            index.setdefault(font_key(family), {}).setdefault(key, str(path))
    return index


def resolve_font_path(name: str | None, *, bold: bool, italic: bool) -> str | None:
    """A TTF on this system for ``name``, or None when nothing honest is available.

    Tries the family's own name first, then the metric-compatible alias table.
    Returns None rather than substituting an arbitrary sans-serif: a measurement
    taken in the wrong typeface is worse than no measurement, because it looks
    like a measurement.
    """
    if not name:
        return None
    index = installed_fonts()
    wanted = style_key(bold=bold, italic=italic)
    for candidate in (font_key(name), *_METRIC_ALIASES.get(name.casefold(), ())):
        styles = index.get(candidate)
        if not styles:
            continue
        for style in (wanted, "regular", *sorted(styles)):
            if style in styles:
                return styles[style]
    return None


@functools.lru_cache(maxsize=64)
def _loaded_font(path: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, _MEASURE_PIXELS)


@functools.lru_cache(maxsize=4096)
def measure_text(path: str, text: str, size_pt: float) -> tuple[float, float]:
    """``(advance width, ascent + descent)`` in points for ``text`` at ``size_pt``."""
    font = _loaded_font(path)
    scale = size_pt / _MEASURE_PIXELS
    ascent, descent = font.getmetrics()
    return (font.getlength(text) * scale, (ascent + descent) * scale)
