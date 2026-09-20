"""The local web server.

Loopback only, token-gated, no external requests, and nothing on disk that the
CLI would not also have written. It is a front end over the same functions the
commands call — `learn`, `run_rules`, `prepare`, `send` — and deliberately not a
reimplementation of any of them: a UI that computed its own answers would be a
second tool to keep honest.

The route list is short on purpose:

* ``POST /api/decks``            upload, load, start rendering
* ``GET  /api/profiles``         the clients already onboarded
* ``POST /api/learn``            name and save the house style derived from a deck
* ``GET  /api/decks/{id}/draft`` the house style derived on upload, unnamed
* ``GET  /api/profiles/{name}``  what was derived, with provenance
* ``POST /api/confirm``          answer the questions, drop facts, write the YAML
* ``POST /api/redact``           what would be sent, offline and without a key
* ``POST /api/check``            the audit, slide by slide
* ``GET  /api/thumbnails/...``   a rendered slide, as a photograph
* ``GET  /api/canvas/...``       a slide's shape tree, for the live surface
* ``GET  /api/media/...``        one picture's own bytes, for the live surface
* ``POST /api/fix``              apply one correction to the open deck
* ``POST /api/move``             put one shape where the person dragged it
* ``POST /api/resize``           give one shape the size a person dragged it to
* ``POST /api/edit-text``        set one run's text to what a person typed
* ``POST /api/undo``             step back one correction
* ``POST /api/reject``           turn one down, for this session only
* ``GET  /api/export/{id}``      the corrected deck
* ``GET  /api/report/{id}``      the self-contained HTML report

The API key, when content review is on, is a field on the ``check`` request and
nothing else. It is not stored, not logged, not echoed, and not written to the
profile.
"""

from __future__ import annotations

import ipaddress
import mimetypes
import zipfile
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from tieout.learn import LearnResult, apply_answers, learn_from_decks
from tieout.learn.emit import write as write_profile
from tieout.model.deck import DeckModel
from tieout.model.loader import DeckLoadError, load_deck
from tieout.model.units import pt_to_emu
from tieout.profile.loader import (
    ProfileError,
    load_for_client,
    load_suppressions,
    profile_dir,
    profile_path,
)
from tieout.profile.schema import Profile
from tieout.report import html as html_report
from tieout.rules.base import AuditResult, clear_caches, run_rules
from tieout_fix import (
    Delta,
    Fix,
    apply_fix,
    delta,
    move_fix,
    plan_fixes,
    resize_fix,
    retext_fix,
)
from tieout_ui.canvas import canvas_view
from tieout_ui.edit import apply_edits, drop
from tieout_ui.session import Deck, SessionStore
from tieout_ui.view import audit_view, find_shape, movable, profile_view, rules_view

__all__ = ["PAGE", "Guard", "create_app", "is_loopback"]

PAGE: Final[Path] = Path(__file__).with_name("static") / "index.html"

#: No external origins at all. The page is one file with inline style and
#: script, which is why 'unsafe-inline' appears; there is no CDN, no font
#: service and no analytics, and the connect-src rule is what enforces that
#: rather than merely asserting it.
_CSP: Final[str] = (
    "default-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

_SECURITY_HEADERS: Final[dict[str, str]] = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _authorise(
    request: Request,
    x_tieout_token: Annotated[str | None, Header()] = None,
    t: str | None = None,
) -> SessionStore:
    """Require the session token on every API call.

    Accepted from a header, or from a query string for the plain ``<img>`` and
    ``<a>`` requests that cannot set one. Compared in constant time, which costs
    nothing and removes the question.

    What this actually defends against is worth being precise about. The page
    itself is not gated — it has to be openable by typing the address — and it
    carries the token in its body, so any process that can already read from
    127.0.0.1 can obtain one. The threat it does stop is the real one for a
    localhost server: a web page on some other origin quietly driving this one.
    Such a page can neither set the custom header nor read the body of a
    cross-origin response, so it cannot start an audit or retrieve a deck.

    Against a hostile process already running on the same machine, this is not a
    defence and is not offered as one. That is why the server refuses to bind
    anything but a loopback address rather than relying on the token.
    """
    import hmac

    store: SessionStore = request.app.state.store
    supplied = x_tieout_token or t or ""
    if not hmac.compare_digest(supplied, store.token):
        raise HTTPException(status_code=401, detail="bad or missing token")
    return store


#: The authorised store, as a route parameter. Module level so that FastAPI can
#: resolve the annotation; see the note in :func:`_authorise`.
Guard = Annotated[SessionStore, Depends(_authorise)]


def is_loopback(host: str) -> bool:
    """Whether a bind address can only be reached from this machine.

    Enforced rather than documented. The whole safety story of this server is
    that it is not reachable from anywhere else — it holds a live deck and, when
    content review is on, accepts an API key — and "we default to localhost" is
    not the same promise as "it cannot bind anything else".
    """
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #


class LearnRequest(BaseModel):
    deck_id: str
    client: str = Field(min_length=1, max_length=120)


class ConfirmRequest(BaseModel):
    client: str = Field(min_length=1, max_length=120)
    #: Question id -> the chosen option.
    answers: dict[str, str] = Field(default_factory=dict)
    #: Profile paths the person disagreed with. Dropping removes the fact and
    #: stops the rule that read it.
    dropped: list[str] = Field(default_factory=list)
    #: Profile paths the person retyped, as ``path -> value``. Validated against
    #: the schema before anything lands, and recorded in the provenance as
    #: hand-set rather than measured. Applied before ``dropped``, so clearing a
    #: field and editing it in the same save resolves to cleared.
    edits: dict[str, Any] = Field(default_factory=dict)
    #: Naming an unsaved draft. The House style tab derives a candidate from the
    #: uploaded deck before anyone presses anything; this is what turns it into
    #: ``profiles/NAME.yaml``.
    deck_id: str = ""


class RedactRequest(BaseModel):
    deck_id: str
    client: str = ""
    forbidden: str = ""
    include_notes: bool = False


class FixRequest(BaseModel):
    deck_id: str
    client: str
    #: The action the correction belongs to, as the review note names it.
    key: str


class MoveRequest(BaseModel):
    """Where a person put a shape.

    ``x_emu`` and ``y_emu`` are absolute, not a delta, and they are integers. The
    page keeps its arithmetic in whole EMU so that a nudge out and a nudge back
    cancel exactly; sending a delta would move that arithmetic onto the server
    and lose the one thing this design guarantees, which is that the number the
    person saw is the number written.
    """

    deck_id: str
    client: str
    slide: int
    shape_id: int
    x_emu: int
    y_emu: int


class ResizeRequest(BaseModel):
    """The size a person dragged a handle to.

    ``cx_emu`` and ``cy_emu`` are absolute, in whole EMU, for the same reason
    :class:`MoveRequest`'s coordinates are: the page's own arithmetic during
    the drag is what has to round-trip exactly, and a delta sent instead would
    move that arithmetic onto the server.
    """

    deck_id: str
    client: str
    slide: int
    shape_id: int
    cx_emu: int
    cy_emu: int


class EditTextRequest(BaseModel):
    """One run's new text, addressed by its exact position.

    ``paragraph`` and ``run`` name a position rather than a pattern -- the
    same numbering :mod:`tieout_ui.canvas` gave the page, counting ``a:r``,
    ``a:br`` and ``a:fld`` together in document order -- so an edit made to
    one occurrence of some words lands on that occurrence and not on every
    place the same words happen to appear.
    """

    deck_id: str
    client: str
    slide: int
    shape_id: int
    paragraph: int
    run: int
    text: str = Field(max_length=10_000)


class UndoRequest(BaseModel):
    deck_id: str
    client: str


class RejectRequest(BaseModel):
    deck_id: str
    client: str
    key: str
    #: False puts it back, so a turned-down correction is not a dead end.
    rejected: bool = True


class CheckRequest(BaseModel):
    deck_id: str
    client: str = ""
    semantic: bool = False
    forbidden: str = ""
    include_notes: bool = False
    #: Set only after the residual list has been shown and accepted.
    approved: bool = False
    #: Held for this request and dropped with it. Never stored anywhere.
    api_key: str | None = Field(default=None, repr=False)
    model: str | None = None


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #


def create_app(store: SessionStore | None = None) -> FastAPI:
    """Build the application. ``store`` is injectable so tests need no server."""
    state = store if store is not None else SessionStore()
    app = FastAPI(title="TieOut", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = state

    @app.middleware("http")
    async def security_headers(request: Any, call_next: Any) -> Response:
        response: Response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    # -- the page -------------------------------------------------------- #

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        """The page, with the session token substituted in.

        The token is delivered in the document rather than in a URL so it does
        not end up in a browser history or in a copied link.
        """
        markup = PAGE.read_text(encoding="utf-8")
        return HTMLResponse(markup.replace("__TIEOUT_TOKEN__", state.token))


    # -- decks ----------------------------------------------------------- #

    @app.post("/api/decks")
    async def upload(
        store: Guard,
        file: Annotated[UploadFile, File()],
    ) -> JSONResponse:
        payload = await file.read()
        try:
            deck = store.add(file.filename or "deck.pptx", payload, load_deck)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DeckLoadError as exc:
            raise HTTPException(status_code=400, detail=f"unreadable deck: {exc}") from exc
        store.render_in_background(deck)
        # Derivation starts here rather than when the House style tab is opened,
        # so that by the time anyone gets there the answer is already waiting.
        store.derive_in_background(deck, _derive)
        return JSONResponse(_deck_payload(deck))

    @app.get("/api/decks/{deck_id}")
    def deck_status(store: Guard, deck_id: str) -> JSONResponse:
        return JSONResponse(_deck_payload(_require(store, deck_id)))

    @app.get("/api/decks/{deck_id}/draft")
    def draft(store: Guard, deck_id: str) -> JSONResponse:
        """The house style derived from this deck, named by nobody yet.

        Polled by the page while derivation runs. Returning ``ready: false``
        rather than blocking keeps the upload response immediate and lets the
        tab say what it is waiting for.
        """
        deck = _require(store, deck_id)
        if deck.draft_error:
            raise HTTPException(status_code=422, detail=deck.draft_error)
        if deck.draft is None:
            return JSONResponse({"ready": False, "deriving": deck.deriving})
        view = profile_view(deck.draft.profile)
        view["ready"] = True
        view["deriving"] = False
        view["draft"] = True
        view["question_summary"] = deck.draft.summary
        return JSONResponse(view)

    @app.get("/api/thumbnails/{deck_id}/{index}")
    def thumbnail(store: Guard, deck_id: str, index: int) -> Response:
        deck = _require(store, deck_id)
        pages = deck.thumbnails.pages
        if not 1 <= index <= len(pages):
            raise HTTPException(status_code=404, detail="no such rendered slide")
        return Response(content=pages[index - 1], media_type="image/png")

    @app.get("/api/canvas/{deck_id}/{index}")
    def canvas(store: Guard, deck_id: str, index: int, client: str) -> JSONResponse:
        """One slide's shape tree, for the live surface to draw.

        Needs a client because :func:`tieout_ui.canvas.canvas_view` flags
        data-mark shapes against the house grid's tolerance -- the one thing
        about a slide's shapes that depends on more than the deck itself.
        """
        deck = _require(store, deck_id)
        profile = _load(client)
        slide = deck.model.slide(index)
        if slide is None:
            raise HTTPException(status_code=404, detail=f"this deck has no slide {index}")
        return JSONResponse(canvas_view(slide, deck.model, profile))

    @app.get("/api/media/{deck_id}/{index}/{uid}")
    def media(store: Guard, deck_id: str, index: int, uid: int) -> Response:
        """One picture's own bytes, straight from the package.

        Keyed on the shape's uid rather than its ``cNvPr`` id, which is not
        always unique on a slide -- the same caution the model itself takes
        anywhere it needs to name one shape rather than describe one.
        """
        deck = _require(store, deck_id)
        slide = deck.model.slide(index)
        if slide is None:
            raise HTTPException(status_code=404, detail=f"this deck has no slide {index}")
        shape = next((s for s in slide.all_shapes() if s.ref.uid == uid), None)
        if shape is None or not shape.image_part_name:
            raise HTTPException(status_code=404, detail="no such picture on that slide")

        with deck.lock:
            try:
                with zipfile.ZipFile(deck.current) as archive:
                    data = archive.read(shape.image_part_name)
            except KeyError as exc:
                raise HTTPException(
                    status_code=404, detail="that image is no longer in the package"
                ) from exc

        content_type = mimetypes.guess_type(shape.image_part_name)[0] or "application/octet-stream"
        return Response(content=data, media_type=content_type)

    # -- profiles -------------------------------------------------------- #

    @app.get("/api/profiles")
    def profiles(store: Guard) -> JSONResponse:
        directory = profile_dir()
        names = (
            sorted(
                path.stem
                for path in directory.glob("*.yaml")
                if not path.stem.endswith(".suppress")
            )
            if directory.is_dir()
            else []
        )
        return JSONResponse({"clients": names, "directory": str(directory)})

    @app.get("/api/profiles/{client}")
    def show_profile(store: Guard, client: str) -> JSONResponse:
        return JSONResponse(profile_view(_load(client)))

    @app.post("/api/learn")
    def learn(store: Guard, request: LearnRequest) -> JSONResponse:
        """Name the derived house style and write it.

        The derivation itself has usually already happened -- it starts on
        upload -- so this is normally just a rename and a write. Falling back to
        deriving here keeps the route correct on its own, for a draft that
        failed or has not finished.
        """
        deck = _require(store, request.deck_id)
        result = deck.draft if deck.draft is not None else _derive(deck.model)
        result.profile.client = request.client
        write_profile(
            result.profile,
            profile_path(request.client),
            questions_deferred=len(result.interview.deferred),
        )
        view = profile_view(result.profile)
        view["question_summary"] = result.summary
        view["written_to"] = str(profile_path(request.client))
        return JSONResponse(view)

    @app.post("/api/confirm")
    def confirm(store: Guard, request: ConfirmRequest) -> JSONResponse:
        profile = _load(request.client)

        for question in profile.questions:
            answer = request.answers.get(question.id)
            if answer is None:
                continue
            question.answer = answer
            question.answered = True
            if question.field_path not in profile.locks:
                profile.locks.append(question.field_path)
            profile.set_provenance(
                question.field_path, f"answered in the UI: {answer}", "high"
            )

        applied, recorded_only = apply_answers(profile)
        # Edits first: a field that is both retyped and cleared in one save
        # should end up cleared, which is the reading that loses no information
        # the person did not deliberately discard.
        edited, rejected = apply_edits(profile, request.edits)
        dropped = drop(profile, request.dropped)

        write_profile(profile, profile_path(request.client))
        view = profile_view(profile)
        view["applied"] = applied
        view["recorded_only"] = recorded_only
        view["dropped"] = dropped
        view["edited"] = edited
        view["rejected"] = rejected
        view["written_to"] = str(profile_path(request.client))
        return JSONResponse(view)

    @app.get("/api/rules")
    def rules(store: Guard) -> JSONResponse:
        return JSONResponse({"rules": rules_view()})

    # -- review ---------------------------------------------------------- #

    @app.post("/api/redact")
    def redact(store: Guard, request: RedactRequest) -> JSONResponse:
        prepared = _prepare(store, request.deck_id, request.client, request.forbidden,
                            request.include_notes)
        return JSONResponse(_plan_payload(prepared))

    @app.post("/api/check")
    def check(store: Guard, request: CheckRequest) -> JSONResponse:
        deck = _require(store, request.deck_id)
        profile = _load(request.client) if request.client else None
        if profile is None:
            raise HTTPException(status_code=400, detail="a client profile is required")

        clear_caches()
        result = run_rules(
            deck.model,
            profile,
            suppressions=load_suppressions(profile.client),
        )

        review: dict[str, Any] | None = None
        before_semantic = len(result.findings)
        if request.semantic:
            review = _semantic(store, request, deck, profile, result)

        view, _ = _audit_payload(
            deck, profile, result, semantic=len(result.findings) - before_semantic
        )
        view["review"] = review
        deck.report = html_report.render(result, deck.model)
        return JSONResponse(view)

    @app.post("/api/fix")
    def fix(store: Guard, request: FixRequest) -> JSONResponse:
        """Apply one correction and re-audit, so the count moves as it is made."""
        deck = _require(store, request.deck_id)
        profile = _load(request.client)
        with deck.lock:
            planned = _plan(deck, profile)
            chosen = planned.get(request.key)
            if chosen is None:
                raise HTTPException(
                    status_code=400, detail="that correction is not available on this deck"
                )

            version = store.next_version(deck)
            report = apply_fix(deck.current, version, chosen)
            if not report.applied:
                version.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail=report.detail)

            store.record(deck, version, chosen.summary)
            _reload(deck)
            payload = _recheck(store, deck, profile, correction=True)
        payload["fixed"] = chosen.summary
        payload["changes"] = report.changes
        return JSONResponse(payload)

    @app.post("/api/move")
    def move(store: Guard, request: MoveRequest) -> JSONResponse:
        """Put one shape where the person dragged it, and re-audit.

        The only route in TieOut that writes geometry, and the reason it may is
        that it computes none of it: the offset is taken from the request and
        written, with no grid consulted and nothing rounded. What the server does
        decide is whether the shape *can* be moved -- a grouped shape's offset is
        in its group's coordinate space, so writing a slide-space number there
        would be wrong -- and whether the number is sane.
        """
        deck = _require(store, request.deck_id)
        profile = _load(request.client)

        with deck.lock:
            shape = find_shape(deck.model, request.slide, request.shape_id)
            if shape is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"slide {request.slide} has no shape {request.shape_id}. "
                        "Re-run the check and try again."
                    ),
                )
            refused = movable(shape)
            if refused:
                raise HTTPException(status_code=422, detail=refused)

            _check_reach(deck.model, request.x_emu, request.y_emu)
            if (request.x_emu, request.y_emu) == (
                pt_to_emu(shape.left_pt),
                pt_to_emu(shape.top_pt),
            ):
                raise HTTPException(
                    status_code=400,
                    detail="that is where the shape already is, so nothing was written.",
                )

            chosen = move_fix(
                slide_index=request.slide,
                shape_id=request.shape_id,
                shape_name=shape.ref.display_name,
                x_emu=request.x_emu,
                y_emu=request.y_emu,
                cx_emu=pt_to_emu(shape.width_pt) or 0,
                cy_emu=pt_to_emu(shape.height_pt) or 0,
                current_x_emu=pt_to_emu(shape.left_pt) or 0,
                current_y_emu=pt_to_emu(shape.top_pt) or 0,
            )
            version = store.next_version(deck)
            report = apply_fix(deck.current, version, chosen)
            if not report.applied:
                version.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail=report.detail)

            store.record(deck, version, chosen.summary)
            deck.moved = True
            _reload(deck)
            # A move is the one correction worth re-rendering for: it is the
            # only one whose result is a position, and a canvas still showing
            # the old one would read as the move having failed.
            store.render_in_background(deck, force=True)
            payload = _recheck(store, deck, profile, correction=True)
        payload["moved"] = chosen.summary
        return JSONResponse(payload)

    @app.post("/api/resize")
    def resize(store: Guard, request: ResizeRequest) -> JSONResponse:
        """Give one shape the size the person dragged a handle to, and re-audit.

        The mirror of ``/api/move``: it computes no size of its own, only
        writes the one it was given, and refuses for the same reason a move
        does -- a grouped shape's extent is in the group's own coordinate
        space, and a shape flagged as a data mark states a value through its
        size or position rather than merely occupying one.
        """
        deck = _require(store, request.deck_id)
        profile = _load(request.client)

        with deck.lock:
            shape = find_shape(deck.model, request.slide, request.shape_id)
            if shape is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"slide {request.slide} has no shape {request.shape_id}. "
                        "Re-run the check and try again."
                    ),
                )
            refused = movable(shape)
            if refused:
                raise HTTPException(status_code=422, detail=refused)

            _check_size_reach(deck.model, request.cx_emu, request.cy_emu)
            if (request.cx_emu, request.cy_emu) == (
                pt_to_emu(shape.width_pt),
                pt_to_emu(shape.height_pt),
            ):
                raise HTTPException(
                    status_code=400,
                    detail="that is the size the shape already is, so nothing was written.",
                )

            chosen = resize_fix(
                slide_index=request.slide,
                shape_id=request.shape_id,
                shape_name=shape.ref.display_name,
                x_emu=pt_to_emu(shape.left_pt) or 0,
                y_emu=pt_to_emu(shape.top_pt) or 0,
                cx_emu=request.cx_emu,
                cy_emu=request.cy_emu,
                current_cx_emu=pt_to_emu(shape.width_pt) or 0,
                current_cy_emu=pt_to_emu(shape.height_pt) or 0,
            )
            version = store.next_version(deck)
            report = apply_fix(deck.current, version, chosen)
            if not report.applied:
                version.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail=report.detail)

            store.record(deck, version, chosen.summary)
            deck.moved = True
            _reload(deck)
            # A resize changes what the slide looks like exactly as a move
            # does, for the same reason: it is a result someone is looking at
            # the canvas to confirm, and a stale image there reads as the
            # resize having failed.
            store.render_in_background(deck, force=True)
            payload = _recheck(store, deck, profile, correction=True)
        payload["resized"] = chosen.summary
        return JSONResponse(payload)

    @app.post("/api/edit-text")
    def edit_text(store: Guard, request: EditTextRequest) -> JSONResponse:
        """Set one run's text to exactly what a person typed, and re-audit.

        Addressed positionally, the same way :mod:`tieout_ui.canvas` numbered
        the run for the page: this paragraph, this run, counting a line break
        and a field alongside a real run in document order. Refused rather
        than guessed at wherever that position does not name an editable run,
        because a stale index -- the deck changed under a page still showing
        the previous check -- must not land on the wrong words.
        """
        deck = _require(store, request.deck_id)
        profile = _load(request.client)

        with deck.lock:
            shape = find_shape(deck.model, request.slide, request.shape_id)
            if shape is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"slide {request.slide} has no shape {request.shape_id}. "
                        "Re-run the check and try again."
                    ),
                )
            if not shape.has_text_frame:
                raise HTTPException(status_code=422, detail="that shape has no text frame")

            paragraphs = shape.text_frame_paragraphs
            if not 0 <= request.paragraph < len(paragraphs):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"paragraph {request.paragraph} does not exist on that shape. "
                        "Re-run the check and try again."
                    ),
                )
            runs = paragraphs[request.paragraph].runs
            if not 0 <= request.run < len(runs):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"run {request.run} does not exist in that paragraph. "
                        "Re-run the check and try again."
                    ),
                )
            if runs[request.run].text == request.text:
                raise HTTPException(
                    status_code=400,
                    detail="that is already the text, so nothing was written.",
                )

            chosen = retext_fix(
                slide_index=request.slide,
                shape_id=request.shape_id,
                shape_name=shape.ref.display_name,
                paragraph=request.paragraph,
                run=request.run,
                text=request.text,
            )
            version = store.next_version(deck)
            report = apply_fix(deck.current, version, chosen)
            if not report.applied:
                version.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail=report.detail)

            store.record(deck, version, chosen.summary)
            # A text edit changes what the rendered slide shows exactly as a
            # move or a resize does, so the fidelity preview is stale in the
            # same way and re-renders for the same reason.
            deck.moved = True
            _reload(deck)
            store.render_in_background(deck, force=True)
            payload = _recheck(store, deck, profile, correction=True)
        payload["edited_text"] = chosen.summary
        return JSONResponse(payload)

    @app.post("/api/undo")
    def undo(store: Guard, request: UndoRequest) -> JSONResponse:
        deck = _require(store, request.deck_id)
        profile = _load(request.client)
        with deck.lock:
            undone = store.undo(deck)
            if undone is None:
                raise HTTPException(status_code=400, detail="nothing to undo")
            _reload(deck)
            if deck.moved:
                # Only decks whose geometry has been edited: everything else
                # this tool applies is a recolour or a text substitution,
                # which is not worth a LibreOffice conversion between a click
                # and its result.
                store.render_in_background(deck, force=True)
            payload = _recheck(store, deck, profile)
        payload["undone"] = undone
        return JSONResponse(payload)

    @app.post("/api/reject")
    def reject(store: Guard, request: RejectRequest) -> JSONResponse:
        deck = _require(store, request.deck_id)
        profile = _load(request.client)
        if request.rejected:
            deck.rejected.add(request.key)
        else:
            deck.rejected.discard(request.key)
        return JSONResponse(_recheck(store, deck, profile))

    @app.get("/api/export/{deck_id}")
    def export(store: Guard, deck_id: str) -> Response:
        """The deck as it now stands, corrections included.

        Reads inside the deck's lock, alongside every correction: a version
        file is not written atomically, so an export that read while one was
        in flight could see it half-written rather than as it was before or
        after.
        """
        deck = _require(store, deck_id)
        with deck.lock:
            name = deck.filename
            if deck.edited:
                stem = name.rsplit(".", 1)[0]
                name = f"{stem} (corrected).pptx"
            content = deck.current.read_bytes()
        return Response(
            content=content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml."
                "presentation"
            ),
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/report/{deck_id}", response_class=HTMLResponse)
    def report(store: Guard, deck_id: str) -> HTMLResponse:
        deck = _require(store, deck_id)
        if not deck.report:
            raise HTTPException(status_code=404, detail="run a check first")
        return HTMLResponse(deck.report)

    return app


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


#: How far outside the canvas a shape may be placed, as a multiple of the slide's
#: own dimensions. Parking a shape off-slide while you work is ordinary, and
#: putting one somewhere it cannot be seen is the person's business -- LO-001
#: will report it either way. What this rejects is a malformed request: a
#: coordinate large enough to write a number PowerPoint will not open.
_MOVE_REACH: Final[int] = 4


def _snap_reach(profile: Profile) -> float:
    """How near a grid line a dragged edge has to be for the line to pull it.

    Not ``grid.tolerance_pt``, and the difference matters. LO-003 reports an edge
    that is *further* than ``tolerance_pt`` from the nearest line -- that is what
    makes it off-grid rather than on it -- and no further than
    ``near_miss_alignment_pt.max``, which is what makes it a near miss rather
    than a different position. A magnet of ``tolerance_pt`` therefore could not
    reach a single shape this editor exists to help place: every one of them
    starts outside it by definition.

    So the reach is the near-miss window, which is the deck's own account of how
    far off a line a shape can be and still be trying to sit on it. Wider than
    that would pull shapes onto lines they are deliberately away from, which is
    the mistake auto-snapping made.
    """
    return max(profile.layout.grid.tolerance_pt, profile.layout.near_miss_alignment_pt.max)


def _check_size_reach(model: DeckModel, cx_emu: int, cy_emu: int) -> None:
    """The same sanity check as a move's, read as a size instead of a position.

    A shape larger than several slides, or of negative or zero size, is not a
    real resize -- it is a malformed request, most likely a stale drag whose
    pointer coordinates were never inside the canvas at all.
    """
    limit_x = int((pt_to_emu(model.width_pt) or 0) * _MOVE_REACH)
    limit_y = int((pt_to_emu(model.height_pt) or 0) * _MOVE_REACH)
    if cx_emu <= 0 or cy_emu <= 0:
        raise HTTPException(
            status_code=422,
            detail="a shape cannot be resized to zero or a negative size.",
        )
    if cx_emu > limit_x or cy_emu > limit_y:
        raise HTTPException(
            status_code=422,
            detail=(
                "that size is larger than any shape is placed in practice, so it "
                "was not written."
            ),
        )


def _check_reach(model: DeckModel, x_emu: int, y_emu: int) -> None:
    limit_x = int((pt_to_emu(model.width_pt) or 0) * _MOVE_REACH)
    limit_y = int((pt_to_emu(model.height_pt) or 0) * _MOVE_REACH)
    if abs(x_emu) > limit_x or abs(y_emu) > limit_y:
        raise HTTPException(
            status_code=422,
            detail=(
                "that position is further from the slide than any shape is placed "
                "in practice, so it was not written."
            ),
        )


def _plan(deck: Deck, profile: Profile) -> dict[str, Fix]:
    """The corrections available on this deck, as it now stands."""
    clear_caches()
    result = run_rules(
        deck.model, profile, suppressions=load_suppressions(profile.client)
    )
    return plan_fixes(result, profile)


def _reload(deck: Deck) -> None:
    """Re-read the deck after a correction, so the next audit sees it.

    Thumbnails are deliberately left as they were. Re-rendering after every fix
    would put a LibreOffice conversion between a click and its result, for images
    that a recoloured fill or a deleted note barely changes.
    """
    deck.model = load_deck(deck.current)


def _recheck(
    store: SessionStore, deck: Deck, profile: Profile, *, correction: bool = False
) -> dict[str, Any]:
    """Re-audit, and say what changed since the audit the page is showing.

    ``correction`` attributes the change to the entry just added to the deck's
    log, which is what lets the export summary say what each correction cost as
    well as what it achieved. An undo passes False: it removes a log entry
    rather than adding one, and its own delta belongs only in the reply.
    """
    clear_caches()
    result = run_rules(
        deck.model, profile, suppressions=load_suppressions(profile.client)
    )
    deck.report = html_report.render(result, deck.model)
    payload, change = _audit_payload(deck, profile, result)
    if correction and deck.log:
        store.note_delta(deck, change)
        payload["corrections"][-1]["delta"] = _delta_payload(change)
    return payload


def _audit_payload(
    deck: Deck, profile: Profile, result: AuditResult, *, semantic: int = 0
) -> tuple[dict[str, Any], Delta]:
    """The audit, with each action told whether it can be corrected.

    Assembled here rather than in the view, which stays a pure function of the
    model: whether a correction can be applied is a fact about this session's
    deck, not about the audit.

    ``semantic`` is how many of ``result``'s findings came from the content
    review rather than from the rules. They are excluded from the comparison
    with the previous audit and sit at the end of ``result.findings``: a
    semantic pass runs on an explicit check and not on the re-audit after a
    correction, so counting them would make a recolour appear to have fixed
    every one of them.
    """
    deterministic = (
        result.findings[: len(result.findings) - semantic]
        if semantic
        else result.findings
    )
    change = delta(deck.last_findings, deterministic)
    deck.last_findings = tuple(deterministic)

    view = audit_view(result, deck.model)
    _mark_change(view, change)
    view["delta"] = _delta_payload(change)
    view["corrections"] = [
        {
            "summary": entry.summary,
            "delta": _delta_payload(entry.delta) if entry.delta else None,
        }
        for entry in deck.log
    ]
    planned = plan_fixes(result, profile)
    for action in view["actions"]:
        fix = planned.get(action["key"])
        action["fixable"] = fix is not None
        action["rejected"] = action["key"] in deck.rejected
    view["edited"] = deck.edited
    view["applied"] = list(deck.applied)
    view["can_undo"] = bool(deck.history)
    # The lines the canvas offers to snap to while a shape is being dragged.
    # Added here rather than in the view because it is a fact about the client's
    # house style, not about this deck -- and because it is the same grid LO-003
    # measures against, so a shape dragged onto a line clears the finding that
    # named it rather than landing somewhere merely near.
    grid = profile.layout.grid
    view["grid"] = {
        "columns_pt": list(grid.columns_pt),
        "rows_pt": list(grid.rows_pt),
        "tolerance_pt": grid.tolerance_pt,
        "snap_pt": _snap_reach(profile),
        "why": profile.provenance.get("layout.grid", ""),
    }
    view["fixable_count"] = sum(
        1
        for action in view["actions"]
        if action["fixable"] and not action["rejected"]
    )
    return view, change


def _delta_payload(change: Delta) -> dict[str, Any]:
    return {
        "fixed": change.fixed,
        "remaining": change.remaining,
        "new": change.new,
        "before": change.before,
        "after": change.after,
        "sentence": change.sentence,
        "worst_new": change.worst_new,
        "arrived": [
            {
                "id": entry.key,
                "rule_id": entry.rule_id,
                "severity": entry.severity,
                "slide": entry.slide,
                "shape": entry.shape,
                "message": entry.message,
            }
            for entry in change.arrived
        ],
    }


def _mark_change(view: dict[str, Any], change: Delta) -> None:
    """Flag the findings this correction exposed, wherever they appear.

    Counting them is not enough. A correction that trades one finding for
    another has to be visible as *which* finding arrived, on the slide it
    arrived on, or the person cannot judge whether to keep the correction or
    undo it -- which is the whole reason the count is broken out.
    """
    arrived = {entry.key for entry in change.arrived}
    if not arrived:
        return
    for slide in view["slides"]:
        for finding in slide["findings"]:
            if finding["id"] in arrived:
                finding["arrived"] = True
    for action in view["actions"]:
        hits = [i for i in action["instances"] if i["id"] in arrived]
        for instance in hits:
            instance["arrived"] = True
        action["arrived"] = len(hits)


def _derive(model: DeckModel) -> LearnResult:
    """Derive a house style from one deck, without naming or writing it.

    The client name is a label the person supplies at save time, so the draft
    carries a placeholder. Named here rather than inlined because the session
    store is handed this as a callable and must not import the learner itself.
    """
    return learn_from_decks([model], "draft")


def _deck_payload(deck: Deck) -> dict[str, Any]:
    return {
        "deck_id": deck.deck_id,
        "filename": deck.filename,
        "slide_count": deck.slide_count,
        "width_pt": deck.model.width_pt,
        "height_pt": deck.model.height_pt,
        "draft": {
            "ready": deck.draft is not None,
            "deriving": deck.deriving,
            "reason": deck.draft_error,
        },
        "thumbnails": {
            "rendering": deck.rendering,
            "available": deck.thumbnails.available,
            "count": len(deck.thumbnails.pages),
            "reason": deck.thumbnails.reason,
            "version": deck.thumbnails_version,
        },
        "slides": [
            {
                "index": slide.index,
                "archetype": slide.archetype,
                "hidden": slide.is_hidden,
            }
            for slide in deck.model.slides
        ],
    }


def _require(store: SessionStore, deck_id: str) -> Deck:
    try:
        return store.require(deck_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such deck in this session") from exc


def _load(client: str) -> Profile:
    try:
        return load_for_client(client, None)
    except ProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _prepare(
    store: SessionStore,
    deck_id: str,
    client: str,
    forbidden: str,
    include_notes: bool,
) -> Any:
    """Build a redaction plan. Imports the review layer only when asked.

    A local import because the UI is useful without it: someone who has not
    installed ``tieout[review]`` still gets the whole deterministic audit, and
    should get a clear message rather than an import error at startup.
    """
    try:
        from tieout_review.review import prepare
        from tieout_review.terms import blocklist_from_text
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise HTTPException(
            status_code=501,
            detail="content review is not installed: pip install 'tieout[review]'",
        ) from exc

    deck = _require(store, deck_id)
    profile = _load(client) if client else None
    return prepare(
        deck.model,
        profile,
        forbidden=blocklist_from_text(forbidden),
        include_notes=include_notes,
    )


def _plan_payload(prepared: Any) -> dict[str, Any]:
    """The redaction plan, as the page shows it.

    ``text`` is included so the page can show the payload verbatim. That is the
    point of the step: someone deciding whether to allow a send should be able
    to read the thing being sent, not a summary of it.
    """
    plan = prepared.plan
    return {
        "characters": prepared.characters,
        "is_clear": plan.is_clear,
        "text": plan.text,
        "redactions": [
            {
                "placeholder": entry.placeholder,
                "kind": entry.kind,
                "original": entry.original,
                "source": entry.source,
                "occurrences": entry.occurrences,
            }
            for entry in plan.redactions
        ],
        "residuals": [
            {
                "text": residual.text,
                "reason": residual.reason,
                "occurrences": residual.occurrences,
                "first_line": residual.first_line,
            }
            for residual in plan.residuals
        ],
        "term_count": len(prepared.terms),
    }


def _build_review_client(model: str | None, api_key: str | None) -> Any:
    """Construct the transport.

    A named seam, so a test can substitute a deterministic stub without
    monkeypatching the SDK or holding a key. It is also the only place in this
    package that touches ``api_key``, and it does not keep it: the value is
    passed straight into the client, which holds it for the life of one request.
    """
    from tieout_review.client import DEFAULT_MODEL, AnthropicReviewClient

    return AnthropicReviewClient(model=model or DEFAULT_MODEL, api_key=api_key)


def _semantic(
    store: SessionStore,
    request: CheckRequest,
    deck: Deck,
    profile: Profile,
    result: Any,
) -> dict[str, Any]:
    """Run the semantic pass, refusing exactly where the CLI refuses.

    The browser does not get to skip the hold: ``approved`` has to have been set
    by someone who was shown the residual list, and the send path re-verifies
    the payload regardless.
    """
    from tieout_review.review import RedactionFailed, RedactionHeld, ResponseError, send

    prepared = _prepare(
        store, request.deck_id, request.client, request.forbidden, request.include_notes
    )
    if not prepared.plan.is_clear and not request.approved:
        payload = _plan_payload(prepared)
        payload["held"] = True
        payload["detail"] = (
            f"{len(prepared.plan.residuals)} item(s) could not be redacted "
            "confidently. Nothing has been sent."
        )
        return payload

    from tieout_review.client import ReviewClientError

    try:
        client = _build_review_client(request.model, request.api_key)
        outcome = send(prepared.approve() if request.approved else prepared, client)
    except (ReviewClientError, RedactionFailed, RedactionHeld, ResponseError) as exc:
        payload = _plan_payload(prepared)
        payload["held"] = True
        payload["detail"] = str(exc)
        return payload

    result.findings.extend(outcome.findings)
    result.rules_run.extend(sorted({finding.rule_id for finding in outcome.findings}))

    payload = _plan_payload(prepared)
    payload["held"] = False
    payload["model"] = outcome.model
    payload["finding_count"] = len(outcome.findings)
    payload["dropped"] = list(outcome.dropped)
    payload["usage"] = outcome.usage
    return payload
