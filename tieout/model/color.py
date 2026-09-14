"""Colour resolution maths: sRGB, HSL, CIE Lab and Delta-E.

Colour comparison across TieOut is CIE76 Delta-E in Lab space, never string
equality. ``#EB0A1E`` and ``#EA0A1F`` are different strings but the same colour to
a human eye, and a brand rule that fires on the second is a false positive that
destroys trust in the tool.

Implemented here rather than pulled in as a dependency because section 3 of the
specification permits no runtime dependencies beyond the listed nine.
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass
from typing import Final

_HEX_RE: Final[re.Pattern[str]] = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$")

# D65 reference white, the illuminant OOXML colours are authored against.
_WHITE_X: Final[float] = 95.047
_WHITE_Y: Final[float] = 100.000
_WHITE_Z: Final[float] = 108.883

_LAB_EPSILON: Final[float] = 216.0 / 24389.0
_LAB_KAPPA: Final[float] = 24389.0 / 27.0


class ColorParseError(ValueError):
    """Raised when a string cannot be read as an sRGB hex triplet."""


@dataclass(frozen=True, slots=True)
class Rgb:
    """An sRGB colour with 8-bit channels."""

    r: int
    g: int
    b: int

    @property
    def hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    def as_tuple(self) -> tuple[int, int, int]:
        """Channels as a plain tuple, for Pillow and other byte-oriented APIs."""
        return (self.r, self.g, self.b)

    @property
    def relative_luminance(self) -> float:
        """WCAG relative luminance, used to decide whether a background is dark."""
        lr, lg, lb = (_srgb_to_linear(c / 255.0) for c in (self.r, self.g, self.b))
        return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb


@dataclass(frozen=True, slots=True)
class Lab:
    """A CIE 1976 L*a*b* colour."""

    lightness: float
    a: float
    b: float


def parse_hex(value: str) -> Rgb:
    """Parse ``RRGGBB``, ``#RRGGBB`` or a 3-digit shorthand into an :class:`Rgb`."""
    match = _HEX_RE.match(value.strip())
    if match is None:
        raise ColorParseError(f"not an sRGB hex colour: {value!r}")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return Rgb(int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def try_parse_hex(value: str | None) -> Rgb | None:
    """Parse, returning None instead of raising. Used on OOXML attributes, which
    are frequently absent or malformed in decks produced by older tooling."""
    if value is None:
        return None
    try:
        return parse_hex(value)
    except ColorParseError:
        return None


def srgb_to_linear(channel: float) -> float:
    """Undo the sRGB transfer function, giving a linear-light channel."""
    return _srgb_to_linear(channel)


def linear_to_srgb(channel: float) -> float:
    """Apply the sRGB transfer function to a linear-light channel."""
    return _linear_to_srgb(channel)


def _srgb_to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(channel: float) -> float:
    if channel <= 0.0031308:
        return channel * 12.92
    return 1.055 * (channel ** (1.0 / 2.4)) - 0.055


def rgb_to_lab(rgb: Rgb) -> Lab:
    """Convert sRGB to CIE Lab via XYZ under a D65 illuminant."""
    lr, lg, lb = (_srgb_to_linear(c / 255.0) for c in (rgb.r, rgb.g, rgb.b))

    x = (0.4124564 * lr + 0.3575761 * lg + 0.1804375 * lb) * 100.0
    y = (0.2126729 * lr + 0.7151522 * lg + 0.0721750 * lb) * 100.0
    z = (0.0193339 * lr + 0.1191920 * lg + 0.9503041 * lb) * 100.0

    fx, fy, fz = (_lab_f(v / w) for v, w in ((x, _WHITE_X), (y, _WHITE_Y), (z, _WHITE_Z)))
    return Lab(116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def _lab_f(ratio: float) -> float:
    if ratio > _LAB_EPSILON:
        return ratio ** (1.0 / 3.0)
    return (_LAB_KAPPA * ratio + 16.0) / 116.0


def delta_e_76(a: Rgb | Lab, b: Rgb | Lab) -> float:
    """CIE76 Delta-E, the Euclidean distance between two colours in Lab space.

    Roughly: 1.0 is a just-noticeable difference under ideal viewing, 2-3 is a
    near-miss a designer would not spot, above 5 is plainly a different colour.
    """
    lab_a = a if isinstance(a, Lab) else rgb_to_lab(a)
    lab_b = b if isinstance(b, Lab) else rgb_to_lab(b)
    return (
        (lab_a.lightness - lab_b.lightness) ** 2
        + (lab_a.a - lab_b.a) ** 2
        + (lab_a.b - lab_b.b) ** 2
    ) ** 0.5


def nearest(
    target: Rgb, candidates: list[Rgb]
) -> tuple[Rgb, float] | None:
    """Return the closest candidate and its Delta-E, or None for an empty list."""
    if not candidates:
        return None
    target_lab = rgb_to_lab(target)
    best = min(candidates, key=lambda c: delta_e_76(target_lab, c))
    return best, delta_e_76(target_lab, best)


# --------------------------------------------------------------------------------------
# DrawingML colour transforms
# --------------------------------------------------------------------------------------
#
# ECMA-376 defines these as percentages in thousandths (val="60000" is 60%).
# ``tint`` and ``shade`` operate on linear RGB; ``lumMod``, ``lumOff`` and
# ``satMod`` operate on HSL. This split is what PowerPoint itself does, and is why
# the theme variant "Accent 1, Lighter 40%" (lumMod 60000 + lumOff 40000) renders
# correctly only when luminance modifiers are applied in HSL.


def apply_tint(rgb: Rgb, amount: float) -> Rgb:
    """Move a colour toward white in linear RGB. ``amount`` is a 0..1 fraction."""
    amount = _clamp01(amount)
    channels = [
        _linear_to_srgb(_srgb_to_linear(c / 255.0) * amount + (1.0 - amount))
        for c in (rgb.r, rgb.g, rgb.b)
    ]
    return _from_unit(channels)


def apply_shade(rgb: Rgb, amount: float) -> Rgb:
    """Move a colour toward black in linear RGB. ``amount`` is a 0..1 fraction."""
    amount = _clamp01(amount)
    channels = [
        _linear_to_srgb(_srgb_to_linear(c / 255.0) * amount) for c in (rgb.r, rgb.g, rgb.b)
    ]
    return _from_unit(channels)


def apply_lum_mod(rgb: Rgb, amount: float) -> Rgb:
    """Scale HSL luminance by ``amount``."""
    hue, lum, sat = _to_hls(rgb)
    return _from_hls(hue, _clamp01(lum * amount), sat)


def apply_lum_off(rgb: Rgb, amount: float) -> Rgb:
    """Add ``amount`` to HSL luminance."""
    hue, lum, sat = _to_hls(rgb)
    return _from_hls(hue, _clamp01(lum + amount), sat)


def apply_sat_mod(rgb: Rgb, amount: float) -> Rgb:
    """Scale HSL saturation by ``amount``."""
    hue, lum, sat = _to_hls(rgb)
    return _from_hls(hue, lum, _clamp01(sat * amount))


def apply_alpha(rgb: Rgb, amount: float, over: Rgb | None = None) -> Rgb:
    """Composite ``rgb`` at ``amount`` opacity over ``over`` (default white).

    TieOut has no compositing model, but a 50%-alpha brand colour measures as a
    different colour on screen, so flattening it against the page keeps BR-004
    honest rather than reporting the unblended value.
    """
    backdrop = over or Rgb(255, 255, 255)
    amount = _clamp01(amount)
    channels = [
        (c * amount + b * (1.0 - amount)) / 255.0
        for c, b in ((rgb.r, backdrop.r), (rgb.g, backdrop.g), (rgb.b, backdrop.b))
    ]
    return _from_unit(channels)


def apply_transform(rgb: Rgb, name: str, amount: float) -> Rgb:
    """Apply one named DrawingML colour transform. Unknown names pass through.

    Passing unknown transforms through unchanged is deliberate: ``hueMod``,
    ``satOff`` and friends are rare, and silently ignoring one shifts a measured
    colour slightly, whereas raising would abort the audit of an entire deck.
    """
    match name:
        case "tint":
            return apply_tint(rgb, amount)
        case "shade":
            return apply_shade(rgb, amount)
        case "lumMod":
            return apply_lum_mod(rgb, amount)
        case "lumOff":
            return apply_lum_off(rgb, amount)
        case "satMod":
            return apply_sat_mod(rgb, amount)
        case "alpha":
            return apply_alpha(rgb, amount)
        case _:
            return rgb


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _to_hls(rgb: Rgb) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(rgb.r / 255.0, rgb.g / 255.0, rgb.b / 255.0)


def _from_hls(hue: float, lum: float, sat: float) -> Rgb:
    return _from_unit(list(colorsys.hls_to_rgb(hue, lum, sat)))


def _from_unit(channels: list[float]) -> Rgb:
    r, g, b = (int(round(_clamp01(c) * 255.0)) for c in channels)
    return Rgb(r, g, b)
