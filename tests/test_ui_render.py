"""Slide rendering, and its absence.

LibreOffice is optional and large. The behaviour that matters is therefore not
the happy path — which is a subprocess call this suite cannot depend on — but
that every failure degrades to "no thumbnails, here is why" instead of breaking
an upload. The conversion step is exercised by faking the binary; the
rasterisation step is exercised against a real PDF.

Stated plainly: the LibreOffice-to-PDF path is not executed here or in CI, and
this container's install is core-only with no Impress filters, so it cannot be.
What is asserted is that its every outcome is handled.
"""

from __future__ import annotations

import pytest

from tieout_ui.render import Renderer, RenderResult, soffice_path


def test_a_result_with_no_pages_is_not_available():
    assert not RenderResult(reason="nope").available
    assert RenderResult(pages=[b"png"]).available


def test_a_missing_binary_is_explained_rather_than_raised(tmp_path, monkeypatch):
    """Someone with no LibreOffice should get slide cards and a sentence, not a
    failed upload."""
    monkeypatch.setattr("tieout_ui.render.shutil.which", lambda _: None)
    result = Renderer(tmp_path).render(tmp_path / "deck.pptx")
    assert not result.available
    assert "not installed" in result.reason
    assert "unaffected" in result.reason


def test_a_conversion_that_produces_nothing_names_the_usual_cause(
    tmp_path, monkeypatch, clean_path
):
    """The failure a minimal install actually gives: `libreoffice-core` without
    `libreoffice-impress` has no PowerPoint filter and says only "source file
    could not be loaded", which helps nobody."""
    monkeypatch.setattr("tieout_ui.render.shutil.which", lambda _: "/usr/bin/soffice")
    monkeypatch.setattr(
        "tieout_ui.render.subprocess.run", lambda *a, **k: None
    )
    result = Renderer(tmp_path).render(clean_path)
    assert not result.available
    assert "libreoffice-impress" in result.reason


def test_a_conversion_that_times_out_is_handled(tmp_path, monkeypatch, clean_path):
    """A deck that wedges LibreOffice must not wedge the UI."""
    import subprocess

    def explode(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="soffice", timeout=1)

    monkeypatch.setattr("tieout_ui.render.shutil.which", lambda _: "/usr/bin/soffice")
    monkeypatch.setattr("tieout_ui.render.subprocess.run", explode)
    result = Renderer(tmp_path).render(clean_path)
    assert not result.available
    assert result.reason


def test_a_missing_binary_on_disk_is_handled(tmp_path, monkeypatch, clean_path):
    monkeypatch.setattr("tieout_ui.render.shutil.which", lambda _: "/nope/soffice")
    result = Renderer(tmp_path).render(clean_path)
    assert not result.available


def test_a_real_pdf_rasterises_to_png(tmp_path):
    """The half of the pipeline that is pure Python, against a real PDF."""
    pytest.importorskip("pypdfium2")
    from PIL import Image

    pdf = tmp_path / "pages.pdf"
    pages = [Image.new("RGB", (960, 540), colour) for colour in ("white", "#eef", "#dde")]
    pages[0].save(pdf, save_all=True, append_images=pages[1:])

    result = Renderer(tmp_path)._rasterise(pdf)
    assert len(result.pages) == 3
    for page in result.pages:
        assert page.startswith(b"\x89PNG"), "the UI serves these as image/png"


def test_an_unreadable_pdf_is_reported_rather_than_raised(tmp_path):
    pytest.importorskip("pypdfium2")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 and then nothing useful")
    result = Renderer(tmp_path)._rasterise(broken)
    assert not result.available
    assert "could not be read" in result.reason


def test_rendering_happens_off_the_upload_response(clean_path, monkeypatch):
    """A conversion takes seconds. Blocking the upload on something optional
    would make the UI feel broken while it did nothing important."""
    from tieout.model.loader import load_deck
    from tieout_ui.session import SessionStore

    monkeypatch.setattr("tieout_ui.render.shutil.which", lambda _: None)
    store = SessionStore()
    try:
        deck = store.add("d.pptx", clean_path.read_bytes(), load_deck)
        store.render_in_background(deck)
        for _ in range(100):
            if not deck.rendering:
                break
            import time

            time.sleep(0.02)
        assert not deck.rendering
        assert deck.thumbnails.reason
    finally:
        store.close()


def test_rendering_is_not_started_twice(clean_path, monkeypatch):
    from tieout.model.loader import load_deck
    from tieout_ui.session import SessionStore

    calls: list[int] = []

    class _CountingRenderer:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def render(self, path: object) -> RenderResult:
            calls.append(1)
            return RenderResult(reason="counted")

    monkeypatch.setattr("tieout_ui.session.Renderer", _CountingRenderer)
    store = SessionStore()
    try:
        deck = store.add("d.pptx", clean_path.read_bytes(), load_deck)
        store.render_in_background(deck)
        import time

        for _ in range(100):
            if deck.thumbnails.reason:
                break
            time.sleep(0.02)
        store.render_in_background(deck)
        time.sleep(0.1)
        assert len(calls) == 1
    finally:
        store.close()


def test_the_binary_lookup_accepts_either_name():
    """Distributions ship it as `soffice`, `libreoffice`, or both."""
    found = soffice_path()
    assert found is None or found.endswith(("soffice", "libreoffice"))
