"""Slide thumbnails, via LibreOffice.

Findings are about places on slides, and a list of findings without the slide in
front of you is a list of coordinates. So the UI renders the deck. There is no
pure-Python way to do that faithfully — laying out PowerPoint is the hard part of
PowerPoint — so this shells out to LibreOffice in headless mode, converts to PDF
once, and rasterises the pages with pdfium.

Three properties matter:

* **Optional.** LibreOffice is a large dependency and plenty of desks will not
  have it. Every failure path here returns "no thumbnails" rather than raising,
  and the UI shows slide cards without images. Nothing about the audit depends
  on it.
* **Offline.** ``soffice`` is invoked with a private profile directory and no
  network, and pdfium is a local library. The rendering path does not weaken the
  air-gap claim.
* **Bounded.** One conversion per deck, into a temporary directory the caller
  owns, with a timeout. A deck that makes LibreOffice hang must not hang the UI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = ["RenderResult", "Renderer", "soffice_path"]

#: Generous, because a first run of LibreOffice builds its profile, and mean
#: enough that a pathological deck cannot wedge the server.
_TIMEOUT_SECONDS: Final[int] = 180

#: Wide enough to read a slide title in a browser, small enough to hold 30 of
#: them in memory without thinking about it.
_THUMBNAIL_WIDTH: Final[int] = 960

_CANDIDATES: Final[tuple[str, ...]] = ("soffice", "libreoffice")


def soffice_path() -> str | None:
    """The LibreOffice binary, or None when it is not installed."""
    for name in _CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


@dataclass
class RenderResult:
    """Rendered pages, or the reason there are none."""

    pages: list[bytes] = field(default_factory=list)
    reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.pages)


@dataclass
class Renderer:
    """Converts a deck to page images once, in a directory the caller owns."""

    work_dir: Path
    width: int = _THUMBNAIL_WIDTH
    timeout: int = _TIMEOUT_SECONDS

    def render(self, deck_path: Path) -> RenderResult:
        binary = soffice_path()
        if binary is None:
            return RenderResult(
                reason=(
                    "LibreOffice is not installed, so slides are shown as cards "
                    "rather than images. Findings are unaffected."
                )
            )
        pdf = self._to_pdf(binary, deck_path)
        if pdf is None:
            # The failure a minimal install actually produces: `libreoffice-core`
            # without `libreoffice-impress` has no PowerPoint filter at all and
            # reports only "source file could not be loaded", which is not a
            # useful thing to show someone.
            return RenderResult(
                reason=(
                    "LibreOffice could not convert this deck. The usual cause is a "
                    "core-only install with no Impress filters — try installing "
                    "libreoffice-impress. Slides are shown as cards instead; "
                    "findings are unaffected."
                )
            )
        return self._rasterise(pdf)

    def _to_pdf(self, binary: str, deck_path: Path) -> Path | None:
        out_dir = self.work_dir / "pdf"
        # Emptied rather than reused. A deck rendered a second time -- which is
        # what happens after a shape is moved -- converts `v1-deck.pptx` beside
        # the earlier `deck.pdf`, and picking the first of two by name would show
        # the version before the move. With one file in the directory there is
        # nothing to pick wrongly, and a conversion that failed leaves none.
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        profile = self.work_dir / "lo-profile"
        try:
            subprocess.run(
                [
                    binary,
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    "--nodefault",
                    "--nofirststartwizard",
                    f"-env:UserInstallation=file://{profile}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(out_dir),
                    str(deck_path),
                ],
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        candidates = sorted(out_dir.glob("*.pdf"))
        return candidates[0] if candidates else None

    def _rasterise(self, pdf: Path) -> RenderResult:
        """Page images for ``pdf``, made in a process of their own.

        PDFium is not thread-safe and cannot be made so from here: see
        :mod:`tieout_ui.rasterise` for why a lock is not enough. The server
        never loads it. A child process does, writes PNGs, and exits.
        """
        out_dir = self.work_dir / "png"
        shutil.rmtree(out_dir, ignore_errors=True)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tieout_ui.rasterise",
                    str(pdf),
                    str(self.width),
                    str(out_dir),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return RenderResult(reason=f"the converted PDF could not be read: {exc}")
        if completed.returncode != 0:
            reason = completed.stderr.strip().splitlines()[-1:] or ["no reason given"]
            return RenderResult(reason=reason[0])
        pages = [path.read_bytes() for path in sorted(out_dir.glob("page-*.png"))]
        if not pages:
            return RenderResult(reason="the converted PDF could not be read: no pages")
        return RenderResult(pages=pages)
