"""EMU <-> point conversion and geometry tolerance helpers.

This module is the single source of truth for unit conversion in TieOut. No other
module may hardcode 12700, 914400 or 72. Every geometric value that leaves the
model layer is expressed in points, because points are what a deck designer sees
in PowerPoint's own position and size dialogue.
"""

from __future__ import annotations

import math
from typing import Final

# OOXML stores lengths in English Metric Units.
EMU_PER_POINT: Final[int] = 12700
EMU_PER_INCH: Final[int] = 914400
EMU_PER_CENTIMETRE: Final[int] = 360000
POINTS_PER_INCH: Final[int] = 72

# OOXML stores font sizes in hundredths of a point (sz="1800" is 18pt) and
# rotations in 60000ths of a degree (rot="5400000" is 90 degrees).
HUNDREDTHS_PER_POINT: Final[int] = 100
SIXTY_THOUSANDTHS_PER_DEGREE: Final[int] = 60000

# Line widths are EMU in OOXML but are conventionally discussed in points.
_DEFAULT_ROUND: Final[int] = 2


def emu_to_pt(emu: int | float | None) -> float | None:
    """Convert EMU to points. Returns None for a None input so callers can
    propagate "not specified" without branching at every call site."""
    if emu is None:
        return None
    return float(emu) / EMU_PER_POINT


def pt_to_emu(pt: float | None) -> int | None:
    """Convert points to EMU, rounded to the nearest integer EMU."""
    if pt is None:
        return None
    return round(float(pt) * EMU_PER_POINT)


def emu_to_inches(emu: int | float | None) -> float | None:
    if emu is None:
        return None
    return float(emu) / EMU_PER_INCH


def inches_to_emu(inches: float | None) -> int | None:
    if inches is None:
        return None
    return round(float(inches) * EMU_PER_INCH)


def pt_to_inches(pt: float | None) -> float | None:
    if pt is None:
        return None
    return float(pt) / POINTS_PER_INCH


def inches_to_pt(inches: float | None) -> float | None:
    if inches is None:
        return None
    return float(inches) * POINTS_PER_INCH


def emu_to_cm(emu: int | float | None) -> float | None:
    if emu is None:
        return None
    return float(emu) / EMU_PER_CENTIMETRE


def hundredths_to_pt(sz: int | str | None) -> float | None:
    """Convert an OOXML ``sz`` attribute (hundredths of a point) to points."""
    if sz is None:
        return None
    return float(int(sz)) / HUNDREDTHS_PER_POINT


def pt_to_hundredths(pt: float | None) -> int | None:
    if pt is None:
        return None
    return round(float(pt) * HUNDREDTHS_PER_POINT)


def ooxml_angle_to_degrees(rot: int | str | None) -> float:
    """Convert an OOXML ``rot`` attribute (60000ths of a degree) to degrees.

    Absent rotation is 0, not None: an unrotated shape is rotated by zero degrees.
    """
    if rot is None:
        return 0.0
    return float(int(rot)) / SIXTY_THOUSANDTHS_PER_DEGREE


def degrees_to_ooxml_angle(degrees: float) -> int:
    return round(degrees * SIXTY_THOUSANDTHS_PER_DEGREE)


def round_pt(value: float | None, ndigits: int = _DEFAULT_ROUND) -> float | None:
    """Round a point value for display and for profile emission.

    Profiles are human-editable, so emitting 872.0000000001 is a defect.
    """
    if value is None:
        return None
    return round(float(value), ndigits)


def approx_equal(a: float | None, b: float | None, tolerance_pt: float) -> bool:
    """Tolerance comparison for point values. None never equals anything,
    including another None -- "unspecified" is not a measurement."""
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tolerance_pt


def rotated_bbox_pt(
    left_pt: float,
    top_pt: float,
    width_pt: float,
    height_pt: float,
    rotation_deg: float,
) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box of a rotated shape, in points.

    PowerPoint rotates a shape about its own centre and does not change its
    ``off``/``ext``, so a rotated shape can extend past the canvas while its
    stored geometry sits comfortably inside it. LO-001 depends on this.

    Returns ``(left, top, width, height)``.
    """
    if not rotation_deg % 360:
        return (left_pt, top_pt, width_pt, height_pt)

    theta = math.radians(rotation_deg)
    cos_t = abs(math.cos(theta))
    sin_t = abs(math.sin(theta))
    new_w = width_pt * cos_t + height_pt * sin_t
    new_h = width_pt * sin_t + height_pt * cos_t
    centre_x = left_pt + width_pt / 2.0
    centre_y = top_pt + height_pt / 2.0
    return (centre_x - new_w / 2.0, centre_y - new_h / 2.0, new_w, new_h)


def rect_intersection_area_pt2(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Overlap area in square points of two ``(left, top, width, height)`` rects."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap_w = min(ax + aw, bx + bw) - max(ax, bx)
    overlap_h = min(ay + ah, by + bh) - max(ay, by)
    if overlap_w <= 0 or overlap_h <= 0:
        return 0.0
    return overlap_w * overlap_h


def rect_area_pt2(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2]) * max(0.0, rect[3])
