"""The layout model, checked against a renderer.

Every geometric rule reads the ink rectangle :mod:`tieout.model.extent` predicts
for a text shape, and that module's promise is that the rectangle contains the
shape's ink. The suite asserts the arithmetic behind that promise; this file
asserts the promise itself, by rendering the reference deck with LibreOffice,
taking every word's box from the PDF, and checking that each word lands inside
the rectangle the model predicted for the frame it sits in.

It found two defects in its first run that 1,365 tests had not: the loader
spelled "centre" where the placer checked "center", so every centred paragraph
was placed at the left margin; and a placeholder's vertical anchor was read from
the slide alone, never from the layout or master that actually set it. Both
were invisible to a fixture built from the same assumptions as the code.

The renderer is LibreOffice, not PowerPoint, so the oracle measures where
LibreOffice puts the ink. That is a second independent implementation of the
same specification, not ground truth, and one divergence is known and excluded
below: LibreOffice centres a ``wrap="none"`` text body on import where
PowerPoint honours its ``algn``. Everything else is compared.

Skipped where ``soffice`` or ``pdftotext`` is missing, which includes CI. Run it
on a machine with both before trusting a change to the model.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tieout.fixtures.generator import build_clean
from tieout.model.extent import ink_extent
from tieout.model.loader import load_deck

pytestmark = pytest.mark.skipif(
    shutil.which("soffice") is None or shutil.which("pdftotext") is None,
    reason="needs LibreOffice (with the Impress filter) and poppler's pdftotext",
)

#: How far outside the predicted rectangle a rendered word may sit before it
#: counts as an escape. A point covers hinting and the renderer's own rounding;
#: the defects this file exists to catch were tens or hundreds of points out.
_TOLERANCE_PT = 1.0

Word = tuple[str, float, float, float, float]


def _render_words(deck_path: Path, work: Path) -> list[list[Word]]:
    """One list of ``(text, xMin, yMin, xMax, yMax)`` per page, in points."""
    out = work / "pdf"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "soffice",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            f"-env:UserInstallation=file://{work / 'lo-profile'}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out),
            str(deck_path),
        ],
        capture_output=True,
        timeout=300,
        check=False,
    )
    pdfs = sorted(out.glob("*.pdf"))
    if not pdfs:
        pytest.skip("LibreOffice produced no PDF; is libreoffice-impress installed?")
    xhtml = work / "words.xhtml"
    subprocess.run(["pdftotext", "-bbox", str(pdfs[0]), str(xhtml)], check=True, timeout=120)
    raw = re.sub(r"<!DOCTYPE[^>]*>", "", xhtml.read_text(encoding="utf-8", errors="replace"))
    raw = raw.replace('xmlns="http://www.w3.org/1999/xhtml"', "")
    pages: list[list[Word]] = []
    for page in ET.fromstring(raw).iter("page"):
        pages.append(
            [
                (
                    word.text or "",
                    float(word.get("xMin", 0)),
                    float(word.get("yMin", 0)),
                    float(word.get("xMax", 0)),
                    float(word.get("yMax", 0)),
                )
                for word in page.iter("word")
            ]
        )
    return pages


def _inside(box: tuple[float, float, float, float], word: Word, pad: float) -> bool:
    left, top, width, height = box
    return (
        word[1] >= left - pad
        and word[3] <= left + width + pad
        and word[2] >= top - pad
        and word[4] <= top + height + pad
    )


def _overshoot(box: tuple[float, float, float, float], word: Word) -> float:
    left, top, width, height = box
    return max(
        0.0,
        word[3] - (left + width),
        left - word[1],
        word[4] - (top + height),
        top - word[2],
    )


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    work = tmp_path_factory.mktemp("oracle")
    deck_path = build_clean(work / "reference_clean.pptx")
    return load_deck(deck_path), _render_words(deck_path, work)


def test_the_renderer_agrees_about_the_canvas(rendered):
    deck, pages = rendered
    assert len(pages) == deck.slide_count, "one PDF page per slide"


def test_every_rendered_word_lands_inside_the_predicted_ink(rendered):
    """The promise, measured. Words belong to the text frame whose box holds
    them; a word the model narrowed a frame around must be inside the narrowed
    rectangle. Frames the model declined to narrow are already the honest
    answer and are not scored."""
    deck, pages = rendered
    escapes: list[str] = []
    scored = 0
    for slide, words in zip(deck.slides, pages, strict=True):
        frames = [
            shape
            for shape in slide.leaf_shapes()
            if shape.has_text and shape.text_frame_paragraphs
        ]
        for word in words:
            owner = next((s for s in frames if _inside(s.visual_bbox_pt, word, 2.0)), None)
            if owner is None:
                continue  # a table cell, a chart label: no text frame to score
            if owner.word_wrap is False:
                continue  # the known LibreOffice divergence, see the module docstring
            extent = ink_extent(owner)
            if extent is None:
                continue
            scored += 1
            if not _inside(extent.bbox, word, _TOLERANCE_PT):
                escapes.append(
                    f"slide {slide.index} {owner.ref.name!r} {word[0][:16]!r} sits "
                    f"{_overshoot(extent.bbox, word):.1f}pt outside "
                    f"({extent.left:.0f},{extent.top:.0f},{extent.width:.0f},{extent.height:.0f})"
                )
    assert scored > 500, f"only {scored} words were scored; the deck or render is wrong"
    assert not escapes, (
        f"{len(escapes)} of {scored} rendered words escape the predicted ink:\n  "
        + "\n  ".join(escapes[:12])
    )
