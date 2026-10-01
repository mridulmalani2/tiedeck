"""Reading a chart's embedded workbook, as far as a range of numbers.

PLAN.md §5.4. A chart in a .pptx holds its values twice: a cache in the chart
part, which is what PowerPoint draws, and the embedded workbook behind it,
which is what PowerPoint *re-reads* the next time anyone opens Edit Data. When
the two disagree the chart shows one thing and will, at the next edit, quietly
redraw itself as the other. :mod:`tieout.model.loader` reads the cache; this
reads the workbook far enough to say whether they agree.

Deliberately narrow. One contiguous range in one column or one row, on a sheet
named in the workbook, with numeric cells read from their stored value (the
``<v>`` a formula also caches). Anything else -- several areas, a whole
column, a defined name, a sheet that is not there -- is a reason, not a guess,
and the caller records it as unchecked.

Stdlib and :mod:`lxml` only, like the rest of :mod:`tieout`: no spreadsheet
library, and nothing that could reach a network.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from typing import Final

from lxml import etree

__all__ = ["read_range"]

_MAIN: Final[str] = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL: Final[str] = "http://schemas.openxmlformats.org/package/2006/relationships"

#: ``Sheet1!$B$2:$B$4`` or ``'My sheet'!B2:B4``, one area only.
_RANGE: Final[re.Pattern[str]] = re.compile(
    r"^(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^!'(),]+))!"
    r"\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?$"
)


def _column_number(letters: str) -> int:
    number = 0
    for char in letters:
        number = number * 26 + (ord(char) - ord("A") + 1)
    return number


def _column_letters(number: int) -> str:
    out = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        out = chr(ord("A") + remainder) + out
    return out


def read_range(xlsx: bytes, formula: str) -> tuple[tuple[float | None, ...] | None, str]:
    """The numbers in ``formula``'s range, in range order, or None and why.

    A cell that is empty or holds text reads as ``None`` -- a gap, as the
    chart's own cache spells one -- rather than as zero.
    """
    match = _RANGE.match(formula.strip())
    if match is None:
        return None, (
            f"the series reads {formula!r}, which is not one range on one sheet"
        )
    sheet_name = (match.group("quoted") or "").replace("''", "'") or match.group("plain")
    first_col, first_row = _column_number(match.group("c1")), int(match.group("r1"))
    last_col = _column_number(match.group("c2") or match.group("c1"))
    last_row = int(match.group("r2") or match.group("r1"))
    if first_col != last_col and first_row != last_row:
        return None, f"the series reads {formula!r}, which spans rows and columns both"

    try:
        archive = zipfile.ZipFile(io.BytesIO(xlsx))
        sheet_xml = _sheet(archive, sheet_name)
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        return None, f"the chart's embedded workbook could not be read ({exc})"
    if sheet_xml is None:
        return None, f"the chart's embedded workbook has no sheet named {sheet_name!r}"

    cells: dict[str, float | None] = {}
    for cell in sheet_xml.iter(f"{{{_MAIN}}}c"):
        reference = cell.get("r", "")
        value = cell.findtext(f"{{{_MAIN}}}v")
        kind = cell.get("t", "n")
        if value is None or kind not in ("n", ""):
            cells[reference] = None
            continue
        try:
            cells[reference] = float(value)
        except ValueError:
            cells[reference] = None

    out: list[float | None] = []
    for column in range(first_col, last_col + 1):
        for row in range(first_row, last_row + 1):
            out.append(cells.get(f"{_column_letters(column)}{row}"))
    return tuple(out), ""


def _sheet(archive: zipfile.ZipFile, name: str) -> etree._Element | None:
    """The worksheet XML for the sheet ``name``, resolved through the workbook."""
    workbook = etree.fromstring(archive.read("xl/workbook.xml"))
    rel_id = None
    for sheet in workbook.iter(f"{{{_MAIN}}}sheet"):
        if sheet.get("name") == name:
            rel_id = sheet.get(f"{{{_REL}}}id")
            break
    if rel_id is None:
        return None
    rels = etree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.iter(f"{{{_PKG_REL}}}Relationship"):
        if rel.get("Id") == rel_id:
            target = rel.get("Target", "")
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
                posixpath.join("xl", target)
            )
            return etree.fromstring(archive.read(path))
    return None
