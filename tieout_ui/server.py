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
* ``GET  /api/thumbnails/...``   a rendered slide
* ``POST /api/fix``              apply one correction to the open deck
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
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from tieout.learn import LearnResult, apply_answers, learn_from_decks
from tieout.learn.emit import write as write_profile
from tieout.model.deck import DeckModel
from tieout.model.loader import DeckLoadError, load_deck
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
from tieout_fix import Fix, apply_fix, plan_fixes
from tieout_ui.edit import apply_edits, drop
from tieout_ui.session import Deck, SessionStore
from tieout_ui.view import audit_view, profile_view, rules_view

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
        if request.semantic:
            review = _semantic(store, request, deck, profile, result)

        view = _audit_payload(deck, profile, result)
        view["review"] = review
        deck.report = html_report.render(result, deck.model)
        return JSONResponse(view)

    @app.post("/api/fix")
    def fix(store: Guard, request: FixRequest) -> JSONResponse:
        """Apply one correction and re-audit, so the count moves as it is made."""
        deck = _require(store, request.deck_id)
        profile = _load(request.client)
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
        payload = _recheck(deck, profile)
        payload["fixed"] = chosen.summary
        payload["changes"] = report.changes
        return JSONResponse(payload)

    @app.post("/api/undo")
    def undo(store: Guard, request: UndoRequest) -> JSONResponse:
        deck = _require(store, request.deck_id)
        profile = _load(request.client)
        undone = store.undo(deck)
        if undone is None:
            raise HTTPException(status_code=400, detail="nothing to undo")
        _reload(deck)
        payload = _recheck(deck, profile)
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
        return JSONResponse(_recheck(deck, profile))

    @app.get("/api/export/{deck_id}")
    def export(store: Guard, deck_id: str) -> Response:
        """The deck as it now stands, corrections included."""
        deck = _require(store, deck_id)
        name = deck.filename
        if deck.edited:
            stem = name.rsplit(".", 1)[0]
            name = f"{stem} (corrected).pptx"
        return Response(
            content=deck.current.read_bytes(),
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


def _recheck(deck: Deck, profile: Profile) -> dict[str, Any]:
    clear_caches()
    result = run_rules(
        deck.model, profile, suppressions=load_suppressions(profile.client)
    )
    deck.report = html_report.render(result, deck.model)
    return _audit_payload(deck, profile, result)


def _audit_payload(
    deck: Deck, profile: Profile, result: AuditResult
) -> dict[str, Any]:
    """The audit, with each action told whether it can be corrected.

    Assembled here rather than in the view, which stays a pure function of the
    model: whether a correction can be applied is a fact about this session's
    deck, not about the audit.
    """
    view = audit_view(result, deck.model)
    planned = plan_fixes(result, profile)
    for action in view["actions"]:
        fix = planned.get(action["key"])
        action["fixable"] = fix is not None
        action["rejected"] = action["key"] in deck.rejected
    view["edited"] = deck.edited
    view["applied"] = list(deck.applied)
    view["can_undo"] = bool(deck.history)
    view["fixable_count"] = sum(
        1
        for action in view["actions"]
        if action["fixable"] and not action["rejected"]
    )
    return view


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
