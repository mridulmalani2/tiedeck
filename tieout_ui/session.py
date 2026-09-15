"""Per-run state, held in memory only.

The UI is a local tool for one person at a desk, so the state store is a dict
with a lock rather than anything with a schema. Two decisions in it are not
casual, though.

**Nothing is persisted that the CLI would not have written.** An uploaded deck
lives in a temporary directory that is deleted when the server stops, and the
only durable artefact is the profile — written to the same `profiles/NAME.yaml`
the CLI writes, because a UI that kept its own copy of a client's house style
would be a second source of truth.

**No session ever holds an API key.** It is accepted on the one request that
needs it and dropped when that request ends. There is no field for it here, and
`tests/test_ui_server.py` asserts that by construction rather than by inspection.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from tieout.model.deck import DeckModel
from tieout_ui.render import Renderer, RenderResult

if TYPE_CHECKING:  # the learner is imported for its type only, never at runtime
    from tieout.learn import LearnResult

__all__ = ["Deck", "SessionStore"]

#: Decks are a few megabytes; a cap keeps a mistyped upload from filling a disk.
MAX_UPLOAD_BYTES: Final[int] = 64 * 1024 * 1024

#: Enough for a desk session, small enough to bound memory when thumbnails are
#: being held for each one.
MAX_DECKS: Final[int] = 8


@dataclass
class Deck:
    """One uploaded deck and whatever has been computed about it."""

    deck_id: str
    filename: str
    path: Path
    model: DeckModel
    thumbnails: RenderResult = field(default_factory=RenderResult)
    rendering: bool = False
    #: The HTML report from this deck's last check. Held per deck rather than
    #: one slot on the app: with two decks open, checking the second used to
    #: make the first one's report link answer 404.
    report: str = ""

    #: What deriving a house style from this deck produces, computed on upload
    #: so the House style tab is populated by the time anyone opens it. Held
    #: unnamed and unwritten: it becomes a profile only when a person names it
    #: and saves. ``draft_error`` carries the reason when derivation failed, so
    #: the page can say so instead of spinning.
    draft: LearnResult | None = None
    draft_error: str = ""
    deriving: bool = False

    @property
    def slide_count(self) -> int:
        return self.model.slide_count


class SessionStore:
    """Uploaded decks for the life of the process.

    ``token`` is the whole of the access control, and it is enough for what this
    is: a loopback-only server on one machine. It is required on every API call
    so that a page open in another tab — or any other process that can reach
    127.0.0.1 — cannot drive the audit or read a deck.
    """

    def __init__(self, *, renderer_width: int = 960) -> None:
        self.token: str = secrets.token_urlsafe(32)
        self._root = Path(tempfile.mkdtemp(prefix="tieout-ui-"))
        self._decks: dict[str, Deck] = {}
        self._lock = threading.Lock()
        self._renderer_width = renderer_width

    # -- lifecycle ------------------------------------------------------- #

    @property
    def root(self) -> Path:
        return self._root

    def close(self) -> None:
        """Delete every uploaded deck. Called when the server stops."""
        with self._lock:
            self._decks.clear()
        shutil.rmtree(self._root, ignore_errors=True)

    # -- decks ----------------------------------------------------------- #

    def add(self, filename: str, payload: bytes, loader: object) -> Deck:
        """Store an upload and load it. Raises ValueError on anything unusable."""
        if not payload:
            raise ValueError("the upload was empty")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"the upload is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"
            )

        safe = _safe_name(filename)
        if not safe.lower().endswith((".pptx", ".potx", ".ppsx")):
            raise ValueError("only .pptx, .potx and .ppsx files can be audited")

        deck_id = secrets.token_urlsafe(8)
        directory = self._root / deck_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / safe
        path.write_bytes(payload)

        model = loader(path)  # type: ignore[operator]
        deck = Deck(deck_id=deck_id, filename=safe, path=path, model=model)

        with self._lock:
            if len(self._decks) >= MAX_DECKS:
                oldest = next(iter(self._decks))
                self._forget(oldest)
            self._decks[deck_id] = deck
        return deck

    def get(self, deck_id: str) -> Deck | None:
        with self._lock:
            return self._decks.get(deck_id)

    def require(self, deck_id: str) -> Deck:
        deck = self.get(deck_id)
        if deck is None:
            raise KeyError(deck_id)
        return deck

    def all(self) -> Iterator[Deck]:
        with self._lock:
            yield from list(self._decks.values())

    def _forget(self, deck_id: str) -> None:
        deck = self._decks.pop(deck_id, None)
        if deck is not None:
            shutil.rmtree(deck.path.parent, ignore_errors=True)

    # -- thumbnails ------------------------------------------------------ #

    def render_in_background(self, deck: Deck) -> None:
        """Start rendering, and return immediately.

        A LibreOffice conversion takes seconds. Blocking the upload response on
        it would make the UI feel broken while it did something optional, so the
        page shows slide cards straight away and swaps in images when they
        arrive.
        """
        if deck.rendering or deck.thumbnails.pages or deck.thumbnails.reason:
            return
        deck.rendering = True

        def work() -> None:
            try:
                deck.thumbnails = Renderer(
                    deck.path.parent, width=self._renderer_width
                ).render(deck.path)
            finally:
                deck.rendering = False

        threading.Thread(target=work, daemon=True, name=f"render-{deck.deck_id}").start()

    # -- the draft house style ------------------------------------------- #

    def derive_in_background(
        self, deck: Deck, derive: Callable[[DeckModel], LearnResult]
    ) -> None:
        """Derive a house style from this deck, and return immediately.

        Started on upload rather than on demand. Deriving takes a few seconds on
        a real deck, and making someone press a button and then watch a spinner
        for the answer the tool could already have had is the difference between
        a tool that feels finished and one that does not.

        Nothing is written. The draft is a candidate the person can look at,
        edit and discard; it becomes ``profiles/NAME.yaml`` only when they name
        it and save.
        """
        if deck.deriving or deck.draft is not None or deck.draft_error:
            return
        deck.deriving = True

        def work() -> None:
            try:
                deck.draft = derive(deck.model)
            except Exception as exc:
                # A deck the deriver cannot read is not a reason to lose the
                # upload: the rest of the UI still works against a saved
                # profile, so the failure belongs on the House style tab.
                deck.draft_error = f"{type(exc).__name__}: {exc}"
            finally:
                deck.deriving = False

        threading.Thread(target=work, daemon=True, name=f"derive-{deck.deck_id}").start()


def _safe_name(filename: str) -> str:
    """The basename, with anything path-like removed.

    An upload's filename is attacker-controlled in the general case and merely
    careless here, but it is concatenated onto a directory either way.
    """
    name = Path(filename.replace("\\", "/")).name
    cleaned = "".join(
        character if character.isalnum() or character in "._- " else "_"
        for character in name
    ).strip(" .")
    return cleaned or "deck.pptx"
