"""Rasterise a PDF to PNG pages, in a process of its own.

Run as ``python -m tieout_ui.rasterise PDF WIDTH OUT_DIR``. Pages land in
``OUT_DIR`` as ``page-0001.png``, ``page-0002.png`` and so on, each ``WIDTH``
pixels wide. A PDF that cannot be read exits 1 with the reason on stderr.

This exists because PDFium, which pypdfium2 wraps, is not safe to use from
more than one thread, and in a server process it is impossible to keep it to
one. Each uploaded deck renders on its own thread, and even with every render
serialised behind a lock the objects pypdfium2 hands back carry finalizers that
CPython runs on whichever thread happens to trigger collection -- so a second
thread reaches into PDFium anyway. The process died of SIGSEGV, SIGABRT or
SIGTRAP depending on where the corruption surfaced, never in CI, which has no
LibreOffice and never gets this far, and only on the machines that matter.

Owning PDFium in a short-lived process is the same arrangement LibreOffice
already has, and it leaves the server with nothing native to be careful about.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: rasterise PDF WIDTH OUT_DIR", file=sys.stderr)
        return 2
    pdf, width, out_dir = Path(argv[0]), int(argv[1]), Path(argv[2])
    try:
        import pypdfium2
    except ImportError:
        print("pypdfium2 is not installed.", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        document = pypdfium2.PdfDocument(pdf)
        try:
            for number, page in enumerate(document, start=1):
                scale = width / max(page.get_width(), 1)
                page.render(scale=scale).to_pil().save(
                    out_dir / f"page-{number:04d}.png", format="PNG"
                )
        finally:
            document.close()
    except Exception as exc:
        print(f"the converted PDF could not be read: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through a subprocess
    sys.exit(main(sys.argv[1:]))
