"""PLAN.md §5.4: a chart's cache against the workbook behind it.

Driven on the §10 deck (Marlin), whose chart python-pptx writes with a cache and
an embedded workbook that agree -- the clean case -- and then with the
workbook, or the chart's own reference into it, rewritten after the fact, which
is what a stale paste or a hand edit to the cache does in practice.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from tests.test_real_world_figures import build
from tieout.learn import learn_from_decks
from tieout.model.loader import load_deck
from tieout.model.workbook import read_range
from tieout.rules.base import clear_caches, run_rules


def _rewrite(path: Path, edit) -> Path:
    """Rewrite parts of a .pptx in place: ``edit(name, data) -> data``."""
    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in items.items():
            archive.writestr(name, edit(name, data))
    return path


def _in_workbook(old: bytes, new: bytes):
    """An edit that rewrites one value inside the embedded workbook's sheet."""

    def edit(name: str, data: bytes) -> bytes:
        if not name.endswith(".xlsx"):
            return data
        inner = zipfile.ZipFile(io.BytesIO(data))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as rebuilt:
            for item in inner.namelist():
                body = inner.read(item)
                if item == "xl/worksheets/sheet1.xml":
                    body = body.replace(old, new)
                rebuilt.writestr(item, body)
        return out.getvalue()

    return edit


def _audit(path: Path):
    clear_caches()
    deck = load_deck(str(path))
    return run_rules(deck, learn_from_decks([deck], "marlin").profile, include=["CH-006"])


@pytest.fixture
def marlin(tmp_path) -> Path:
    return build(tmp_path / "marlin.pptx")


def test_a_chart_whose_workbook_agrees_is_silent(marlin) -> None:
    result = _audit(marlin)
    assert not result.findings
    assert not result.unchecked


def test_a_workbook_that_disagrees_with_the_drawn_chart_is_reported(marlin) -> None:
    _rewrite(marlin, _in_workbook(b"<v>1935</v>", b"<v>1905</v>"))
    (finding,) = _audit(marlin).findings
    assert finding.slide_index == 9
    assert "'Revenue' for FY25A draws 1,935 where Sheet1!$B$2:$B$4 holds 1,905" in (
        finding.message
    )
    assert finding.severity == "major"


def test_a_range_on_a_sheet_the_workbook_lacks_is_declined(marlin) -> None:
    _rewrite(
        marlin,
        lambda name, data: data.replace(b"Sheet1!$B$2:$B$4", b"Missing!$B$2:$B$4")
        if "charts/chart" in name
        else data,
    )
    result = _audit(marlin)
    assert not result.findings
    assert any("no sheet named 'Missing'" in r.reason for r in result.unchecked)


def test_a_range_of_several_areas_is_declined(marlin) -> None:
    _rewrite(
        marlin,
        lambda name, data: data.replace(
            b"<c:f>Sheet1!$B$2:$B$4</c:f>", b"<c:f>(Sheet1!$B$2,Sheet1!$B$4)</c:f>"
        )
        if "charts/chart" in name
        else data,
    )
    result = _audit(marlin)
    assert not result.findings
    assert any("not one range on one sheet" in r.reason for r in result.unchecked)


def test_read_range_reads_a_quoted_sheet_and_a_row(tmp_path, marlin) -> None:
    with zipfile.ZipFile(marlin) as archive:
        (name,) = [n for n in archive.namelist() if n.endswith(".xlsx")]
        xlsx = archive.read(name)
    values, note = read_range(xlsx, "'Sheet1'!$B$2:$B$4")
    assert (values, note) == ((1352.0, 1624.0, 1935.0), "")
    # A row range across the header: B1 holds the series name, a string.
    row, _ = read_range(xlsx, "Sheet1!$A$1:$B$1")
    assert row == (None, None)
    assert re.search("spans rows and columns", read_range(xlsx, "Sheet1!A1:B2")[1])
