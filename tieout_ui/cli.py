"""``tieout-ui`` — serve the local page.

Loopback only, and enforced rather than defaulted: a non-loopback ``--host`` is
refused outright. The server holds a live deck and, with content review on,
accepts an API key, so "we default to localhost" is not a strong enough promise.
Anyone who genuinely needs this reachable from elsewhere should put it behind
something that can authenticate, not widen this.
"""

from __future__ import annotations

import socket
from typing import Annotated

import typer
from rich.console import Console

from tieout.cli import EXIT_ERROR

app = typer.Typer(add_completion=False, help="Serve the TieOut page on this machine only.")
_out = Console()
_err = Console(stderr=True)


@app.command()
def serve(
    host: Annotated[
        str, typer.Option("--host", help="Bind address. Loopback addresses only.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port to bind.")] = 8765,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open a browser on start.")
    ] = True,
) -> None:
    """Start the local UI."""
    from tieout_ui.server import create_app, is_loopback
    from tieout_ui.session import SessionStore

    if not is_loopback(host):
        _err.print(
            f"[bold red]error[/bold red] {host!r} is not a loopback address. This server "
            "has no authentication beyond a session token and holds live deck material; "
            "it will only bind an address reachable from this machine."
        )
        raise typer.Exit(EXIT_ERROR)

    if _in_use(host, port):
        _err.print(
            f"[bold red]error[/bold red] {host}:{port} is already in use. "
            "Pass --port to choose another."
        )
        raise typer.Exit(EXIT_ERROR)

    store = SessionStore()
    application = create_app(store)
    url = f"http://{host}:{port}/"

    _out.print()
    _out.print("[bold]TieOut[/bold] is serving on this machine only.")
    _out.print(f"  {url}")
    _out.print(
        "[dim]The link carries a session token, so open it from here rather than "
        "retyping the address. Uploaded decks live in a temporary directory and are "
        "deleted when this stops.[/dim]"
    )
    _out.print()

    if open_browser:
        _launch(url, store.token)

    import uvicorn

    try:
        uvicorn.run(application, host=host, port=port, log_level="warning")
    finally:
        store.close()


def _in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def _launch(url: str, token: str) -> None:  # pragma: no cover - opens a browser
    """Open the page with the token in the query string.

    The one place a token appears in a URL. The page immediately replaces its own
    address so the token does not stay in the browser's history or in anything
    the user might copy.
    """
    import threading
    import webbrowser

    threading.Timer(0.6, lambda: webbrowser.open(f"{url}?t={token}")).start()


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
