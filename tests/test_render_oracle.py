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
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tieout.fixtures.generator import build_clean, build_dirty
from tieout.model.deck import ShapeModel
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


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("\u2019", "'").replace("\u2014", "-")


def _owner(frames: list[ShapeModel], word: Word) -> ShapeModel | None:
    """The text frame a rendered word belongs to, or None when that is not certain.

    Containment alone is not enough. The dirty deck drags a footnote up into a
    column's frame, and a financial table sits inside another's; attributing
    their words to the column made its ink box "wrong" and its 11pt size
    "unexplained" by 7pt words. A word belongs to a frame whose box holds it
    *and* whose own text contains it, and when more than one frame qualifies
    nothing is claimed. Table and chart words never qualify, since no text
    frame contains them, which is what leaves them unscored.
    """
    text = _normalise(word[0]).strip()
    if not text:
        return None
    bare = text.strip(".,;:!?()[]\"'")
    owners = []
    for shape in frames:
        if not _inside(shape.visual_bbox_pt, word, 2.0):
            continue
        own = _normalise(" ".join(p.text for p in shape.text_frame_paragraphs))
        if text in own or (bare and bare in own):
            owners.append(shape)
    return owners[0] if len(owners) == 1 else None


def _overshoot(box: tuple[float, float, float, float], word: Word) -> float:
    left, top, width, height = box
    return max(
        0.0,
        word[3] - (left + width),
        left - word[1],
        word[4] - (top + height),
        top - word[2],
    )


@pytest.fixture(scope="module", params=("clean", "dirty"))
def rendered(request, tmp_path_factory):
    """Both reference decks. The dirty one carries every seeded defect -- shapes
    off the canvas, overlaps, a logo out of place -- and the bound has to hold
    for those too: a defect is exactly where the model is asked to be right."""
    work = tmp_path_factory.mktemp(f"oracle-{request.param}")
    build = build_clean if request.param == "clean" else build_dirty
    deck_path = build(work / f"reference_{request.param}.pptx")
    return load_deck(deck_path), _render_words(deck_path, work)


def _visible(deck):
    """LibreOffice leaves hidden slides out of a PDF export, so pages pair with
    the slides that are shown, in order. The dirty deck seeds a hidden slide."""
    return [slide for slide in deck.slides if not getattr(slide, "is_hidden", False)]


def test_the_renderer_agrees_about_the_canvas(rendered):
    deck, pages = rendered
    assert len(pages) == len(_visible(deck)), "one PDF page per visible slide"


def test_every_rendered_word_lands_inside_the_predicted_ink(rendered):
    """The promise, measured. Words belong to the text frame whose box holds
    them; a word the model narrowed a frame around must be inside the narrowed
    rectangle. Frames the model declined to narrow are already the honest
    answer and are not scored."""
    deck, pages = rendered
    escapes: list[str] = []
    scored = 0
    for slide, words in zip(_visible(deck), pages, strict=True):
        frames = [
            shape
            for shape in slide.leaf_shapes()
            if shape.has_text and shape.text_frame_paragraphs
        ]
        for word in words:
            owner = _owner(frames, word)
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


def test_every_resolved_font_size_agrees_with_the_render(rendered):
    """A word's rendered height is its font size times a ratio the face fixes,
    a little over one for every Latin face LibreOffice substitutes here. So a
    resolved size that is wrong -- a run that fell through to the 18pt default,
    a theme token resolved to the wrong face's size, an autofit scale applied
    twice -- shows up as a height the size does not explain.

    Frames mixing sizes are skipped, because nothing says which run a word
    came from. On the reference deck that leaves 1,235 words, whose heights sit
    at 1.163 times their resolved size to three decimal places.
    """
    deck, pages = rendered
    compared = 0
    wrong: list[str] = []
    for slide, words in zip(_visible(deck), pages, strict=True):
        frames = [
            shape
            for shape in slide.leaf_shapes()
            if shape.has_text and shape.text_frame_paragraphs
        ]
        for word in words:
            owner = _owner(frames, word)
            if owner is None:
                continue
            sizes = {
                run.font.size_pt
                for paragraph in owner.text_frame_paragraphs
                for run in paragraph.runs
                if run.font is not None and run.font.size_pt and run.text.strip()
            }
            if len(sizes) != 1:
                continue
            size = next(iter(sizes))
            compared += 1
            ratio = (word[4] - word[2]) / size
            if not 0.85 <= ratio <= 1.45:
                wrong.append(
                    f"slide {slide.index} {owner.ref.name!r} {word[0][:14]!r}: resolved "
                    f"{size:g}pt, rendered {word[4] - word[2]:.1f}pt tall (x{ratio:.2f})"
                )
    assert compared > 500, f"only {compared} words compared"
    assert not wrong, (
        f"{len(wrong)} words render at a height their resolved size does not "
        "explain:\n  " + "\n  ".join(wrong[:12])
    )
