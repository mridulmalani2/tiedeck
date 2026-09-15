"""The local server, driven through the app rather than a socket.

Three things are worth testing here and the rest is plumbing: that the token
actually gates everything, that the bind address cannot be widened, and that no
API key is ever retained or echoed. Everything else in this package is a view
over functions already tested elsewhere, and the tests reflect that.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tieout.model.loader import load_deck
from tieout.profile.loader import PROFILE_DIR_ENV
from tieout_ui.server import create_app, is_loopback
from tieout_ui.session import MAX_UPLOAD_BYTES, SessionStore


@pytest.fixture
def store():
    state = SessionStore()
    yield state
    state.close()


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "profiles"))
    with TestClient(create_app(store)) as test_client:
        test_client.headers.update({"X-TieOut-Token": store.token})
        yield test_client


@pytest.fixture
def uploaded(client, clean_path):
    response = client.post(
        "/api/decks",
        files={"file": (clean_path.name, clean_path.read_bytes(), _MIME)},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def onboarded(client, uploaded):
    response = client.post(
        "/api/learn", json={"deck_id": uploaded["deck_id"], "client": "demo"}
    )
    assert response.status_code == 200, response.text
    return response.json()


_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# --------------------------------------------------------------------------- #
# Reachability
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.9.9.9"])
def test_a_loopback_address_is_accepted(host):
    assert is_loopback(host)


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "192.168.1.5", "10.0.0.1", "example.com", "", "::"]
)
def test_anything_reachable_from_elsewhere_is_refused(host):
    """Enforced, not defaulted. This server holds a live deck and, with content
    review on, takes an API key; "we default to localhost" is a weaker promise
    than "it will not bind anything else"."""
    assert not is_loopback(host)


def test_the_cli_refuses_a_non_loopback_host():
    from typer.testing import CliRunner

    from tieout.cli import EXIT_ERROR
    from tieout_ui.cli import app

    result = CliRunner().invoke(app, ["--host", "0.0.0.0"], catch_exceptions=False)
    assert result.exit_code == EXIT_ERROR
    assert "loopback" in result.output


# --------------------------------------------------------------------------- #
# The token
# --------------------------------------------------------------------------- #


API_PATHS = [
    ("GET", "/api/profiles"),
    ("GET", "/api/rules"),
    ("GET", "/api/decks/anything"),
    ("GET", "/api/thumbnails/anything/1"),
    ("GET", "/api/report/anything"),
    ("POST", "/api/learn"),
    ("POST", "/api/confirm"),
    ("POST", "/api/redact"),
    ("POST", "/api/check"),
]


@pytest.mark.parametrize(("method", "path"), API_PATHS)
def test_every_api_route_needs_the_token(store, method, path):
    """Any other process that can reach 127.0.0.1 must not be able to drive an
    audit or read a deck."""
    with TestClient(create_app(store)) as anonymous:
        response = anonymous.request(method, path, json={})
    assert response.status_code == 401, path


def test_a_wrong_token_is_refused(store):
    with TestClient(create_app(store)) as impostor:
        response = impostor.get("/api/rules", headers={"X-TieOut-Token": "x" * 43})
    assert response.status_code == 401


def test_a_token_in_the_query_string_works_for_images_and_links(client, store, uploaded):
    """An <img> tag cannot set a header, so those routes accept ?t= as well."""
    bare = client.get(
        f"/api/decks/{uploaded['deck_id']}?t={store.token}", headers={"X-TieOut-Token": ""}
    )
    assert bare.status_code == 200


def test_the_page_carries_the_token_and_no_external_reference(client, store):
    response = client.get("/")
    assert response.status_code == 200
    assert store.token in response.text
    assert "__TIEOUT_TOKEN__" not in response.text
    for forbidden in ("http://", "https://", "//cdn", "googleapis", "gstatic"):
        assert forbidden not in response.text, forbidden


def test_the_page_drops_the_token_from_the_address_bar(client):
    """Or it stays in the browser's history and in anything copied from it."""
    assert "history.replaceState" in client.get("/").text


def test_every_response_carries_a_locked_down_policy(client):
    headers = client.get("/").headers
    policy = headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #


def test_a_deck_uploads_and_reports_its_slides(uploaded):
    assert uploaded["slide_count"] == 26
    assert len(uploaded["slides"]) == 26
    assert uploaded["slides"][0]["archetype"] == "title"


def test_a_non_deck_is_refused(client):
    response = client.post("/api/decks", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 400
    assert "pptx" in response.json()["detail"]


def test_an_empty_upload_is_refused(client):
    response = client.post("/api/decks", files={"file": ("empty.pptx", b"", _MIME)})
    assert response.status_code == 400


def test_a_corrupt_deck_is_refused_with_a_readable_message(client):
    response = client.post("/api/decks", files={"file": ("broken.pptx", b"PK\x03\x04junk", _MIME)})
    assert response.status_code == 400
    assert "deck" in response.json()["detail"].lower()


def test_a_path_in_the_filename_cannot_escape_the_session_directory(client, store, clean_path):
    response = client.post(
        "/api/decks",
        files={"file": ("../../../escape.pptx", clean_path.read_bytes(), _MIME)},
    )
    assert response.status_code == 200
    deck = store.require(response.json()["deck_id"])
    assert deck.path.parent.parent == store.root
    assert deck.filename == "escape.pptx"


def test_the_upload_cap_is_stated_in_megabytes_not_bytes():
    assert MAX_UPLOAD_BYTES % (1024 * 1024) == 0


def test_an_unknown_deck_is_a_404_not_a_500(client):
    assert client.get("/api/decks/nope").status_code == 404


def test_a_thumbnail_index_outside_the_deck_is_a_404(client, uploaded):
    assert client.get(f"/api/thumbnails/{uploaded['deck_id']}/999").status_code == 404


def test_the_page_says_why_there_are_no_thumbnails_rather_than_failing(uploaded):
    """LibreOffice is optional. Its absence must read as an explanation, not as
    a broken upload."""
    thumbnails = uploaded["thumbnails"]
    assert thumbnails["available"] or thumbnails["rendering"] or thumbnails["reason"]


# --------------------------------------------------------------------------- #
# Learning and confirming
# --------------------------------------------------------------------------- #


def test_learning_writes_the_same_profile_the_cli_writes(onboarded, tmp_path):
    """One source of truth for a client's house style, or there are two tools."""
    written = tmp_path / "profiles" / "demo.yaml"
    assert written.is_file()
    assert onboarded["written_to"] == str(written)


def test_the_derived_profile_is_shown_with_its_evidence(onboarded):
    """Someone can only disagree with a claim they can see the reason for."""
    titles = {group["title"] for group in onboarded["groups"]}
    assert {"Colour", "Type", "Layout"} <= titles
    palette = next(
        fact
        for group in onboarded["groups"]
        for fact in group["facts"]
        if fact["path"] == "brand.palette_hex"
    )
    assert palette["kind"] == "swatches"
    assert palette["value"]
    assert palette["why"], "a derived fact without its provenance cannot be judged"


def test_the_reference_deck_raises_no_questions(onboarded):
    """The acceptance criterion the whole design turns on, seen from the UI."""
    assert [q for q in onboarded["questions"] if not q["answered"]] == []


def test_an_existing_client_can_be_listed_and_loaded(client, onboarded):
    listed = client.get("/api/profiles").json()
    assert "demo" in listed["clients"]
    assert client.get("/api/profiles/demo").json()["client"] == "demo"


def test_an_unknown_client_is_a_404(client):
    assert client.get("/api/profiles/nobody").status_code == 404


def test_dropping_a_fact_records_it_and_stops_the_rule(client, onboarded, tmp_path):
    """Dropping is the only edit offered, and it is always safe: the rule that
    read the field stops running and the profile says a person decided so."""
    response = client.post(
        "/api/confirm", json={"client": "demo", "dropped": ["brand.palette_hex"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dropped"] == ["brand.palette_hex"]
    keys = {entry["key"] for entry in body["not_learned"]}
    assert "brand.palette_hex" in keys

    from tieout.profile.loader import load

    profile = load(tmp_path / "profiles" / "demo.yaml")
    assert profile.brand.palette_hex == []
    assert "brand.palette_hex" in profile.locks


def test_dropping_a_path_that_does_not_exist_is_ignored(client, onboarded):
    response = client.post(
        "/api/confirm", json={"client": "demo", "dropped": ["brand.nonsense.path"]}
    )
    assert response.json()["dropped"] == []


def test_an_answered_question_reaches_the_field(client, uploaded, dirty_path):
    """The UI shares the fix for the review bug rather than reimplementing it."""
    upload = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()
    client.post("/api/learn", json={"deck_id": upload["deck_id"], "client": "messy"})
    profile = client.get("/api/profiles/messy").json()
    hygiene = [
        q for q in profile["questions"] if q["field_path"].startswith("hygiene.allow")
    ]
    if not hygiene:
        pytest.skip("the dirty fixture raised no hygiene question")
    question = hygiene[0]
    permissive = next(o for o in question["options"] if o.startswith(("yes", "no, allow")))
    body = client.post(
        "/api/confirm", json={"client": "messy", "answers": {question["id"]: permissive}}
    ).json()
    assert question["field_path"] in body["applied"]


# --------------------------------------------------------------------------- #
# The audit
# --------------------------------------------------------------------------- #


def test_the_reference_deck_checks_clean_against_its_own_profile(client, onboarded, uploaded):
    response = client.post(
        "/api/check", json={"deck_id": uploaded["deck_id"], "client": "demo"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] == 0
    assert body["review"] is None


def test_findings_arrive_slide_by_slide(client, onboarded, dirty_path):
    """Slide-wise because that is the order a deck gets fixed in."""
    upload = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()
    body = client.post(
        "/api/check", json={"deck_id": upload["deck_id"], "client": "demo"}
    ).json()
    assert body["summary"]["total"] > 0
    assert len(body["slides"]) == upload["slide_count"]
    with_findings = [slide for slide in body["slides"] if slide["findings"]]
    assert with_findings
    for slide in with_findings:
        assert slide["worst"] in ("blocker", "major", "minor", "info")
    finding = with_findings[0]["findings"][0]
    assert {"rule_id", "severity", "message", "category"} <= set(finding)


def test_a_check_without_a_client_is_refused(client, uploaded):
    response = client.post("/api/check", json={"deck_id": uploaded["deck_id"]})
    assert response.status_code == 400


def test_the_report_is_self_contained(client, onboarded, uploaded):
    client.post("/api/check", json={"deck_id": uploaded["deck_id"], "client": "demo"})
    markup = client.get(f"/api/report/{uploaded['deck_id']}").text
    for forbidden in ("http://", "https://", "<script"):
        assert forbidden not in markup


def test_the_report_needs_a_check_first(client, uploaded):
    assert client.get(f"/api/report/{uploaded['deck_id']}").status_code == 404


# --------------------------------------------------------------------------- #
# Content review
# --------------------------------------------------------------------------- #


def test_the_redaction_preview_needs_no_key_and_sends_nothing(
    client, onboarded, uploaded, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post(
        "/api/redact",
        json={
            "deck_id": uploaded["deck_id"],
            "client": "demo",
            "forbidden": "Ashcombe Partners",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["redactions"]
    assert "Ashcombe" not in body["text"], "the previewed payload must be redacted"
    assert body["characters"] > 0


def test_the_preview_shows_the_payload_verbatim(client, onboarded, uploaded):
    """The point of the step is reading the thing, not a summary of it."""
    body = client.post(
        "/api/redact", json={"deck_id": uploaded["deck_id"], "client": "demo"}
    ).json()
    assert "1,908" in body["text"], "the figures are what the model is shown"


def test_speaker_notes_stay_out_of_the_payload_unless_asked(client, onboarded, dirty_path):
    upload = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()
    without = client.post(
        "/api/redact", json={"deck_id": upload["deck_id"], "client": "demo"}
    ).json()
    with_notes = client.post(
        "/api/redact",
        json={"deck_id": upload["deck_id"], "client": "demo", "include_notes": True},
    ).json()
    assert with_notes["characters"] > without["characters"]


def test_an_unapproved_residual_list_stops_the_send(client, onboarded, dirty_path, monkeypatch):
    """The browser does not get to skip the hold.

    Run against the dirty fixture, which carries text the redactor cannot clear
    confidently. Asserted on the transport never being reached rather than on an
    error type: "refused" should mean nothing was sent, not that something was
    sent and then complained about.
    """
    calls: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("nothing should have been sent")

    import tieout_review.review as review_module

    monkeypatch.setattr(review_module, "send", refuse)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    upload = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()
    preview = client.post(
        "/api/redact", json={"deck_id": upload["deck_id"], "client": "demo"}
    ).json()
    assert preview["residuals"], "the dirty fixture is expected to leave a residual"

    response = client.post(
        "/api/check",
        json={
            "deck_id": upload["deck_id"],
            "client": "demo",
            "semantic": True,
            "approved": False,
        },
    )
    assert response.status_code == 200
    review = response.json()["review"]
    assert review["held"] is True
    assert calls == [], "the transport must not have been reached"
    assert "Nothing has been sent" in review["detail"]


def test_a_semantic_finding_is_labelled_and_reaches_the_slide(
    client, onboarded, uploaded, monkeypatch
):
    class _Stub:
        model = "stub-model"

        def complete(self, system, user, schema):
            answer = {
                "findings": [
                    {
                        "rule": "SE-001",
                        "slide": 4,
                        "quote": "the headline",
                        "explanation": "It says 20%; the table shows 8%.",
                        "confidence": "high",
                    }
                ]
            }
            return json.dumps(answer), {"input_tokens": 10, "output_tokens": 2}

    import tieout_ui.server as server_module

    monkeypatch.setattr(server_module, "_build_review_client", lambda *a, **k: _Stub())
    body = client.post(
        "/api/check",
        json={
            "deck_id": uploaded["deck_id"],
            "client": "demo",
            "semantic": True,
            "approved": True,
        },
    ).json()
    review = body["review"]
    assert review["held"] is False
    assert review["model"] == "stub-model"
    slide = next(s for s in body["slides"] if s["index"] == 4)
    assert any(f["category"] == "semantic" for f in slide["findings"])


def test_a_missing_key_is_reported_rather_than_raised(client, onboarded, uploaded, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post(
        "/api/check",
        json={
            "deck_id": uploaded["deck_id"],
            "client": "demo",
            "semantic": True,
            "approved": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["review"]["held"] is True


# --------------------------------------------------------------------------- #
# The key
# --------------------------------------------------------------------------- #


def test_no_session_object_can_hold_an_api_key(store):
    """Asserted structurally rather than by inspection: there is no field for it."""
    from dataclasses import fields

    from tieout_ui.session import Deck

    names = {field.name for field in fields(Deck)} | set(vars(store))
    assert not any("key" in name.lower() or "secret" in name.lower() for name in names)


def test_the_key_is_never_echoed_back(client, onboarded, uploaded, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post(
        "/api/check",
        json={
            "deck_id": uploaded["deck_id"],
            "client": "demo",
            "semantic": True,
            "approved": True,
            "api_key": "sk-ant-do-not-echo-me",
        },
    )
    assert "do-not-echo-me" not in response.text


def test_the_key_is_not_written_anywhere_under_the_session(
    client, onboarded, uploaded, store, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client.post(
        "/api/check",
        json={
            "deck_id": uploaded["deck_id"],
            "client": "demo",
            "semantic": True,
            "approved": True,
            "api_key": "sk-ant-do-not-persist-me",
        },
    )
    for path in store.root.rglob("*"):
        if path.is_file():
            assert b"do-not-persist-me" not in path.read_bytes(), path


def test_the_page_never_stores_the_key_in_the_browser(client):
    """No localStorage, no sessionStorage, no cookie."""
    page = client.get("/").text
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in page, forbidden


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #


def test_closing_the_store_deletes_every_uploaded_deck(clean_path):
    state = SessionStore()
    state.add("d.pptx", clean_path.read_bytes(), load_deck)
    root = state.root
    assert root.exists()
    state.close()
    assert not root.exists()


# --------------------------------------------------------------------------- #
# Starting it
# --------------------------------------------------------------------------- #


def _serve(monkeypatch, args: list[str]):
    from typer.testing import CliRunner

    import tieout_ui.cli as cli_module

    started: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli_module,
        "_launch",
        lambda url, token: started.append({"opened": url}),
    )

    class _FakeUvicorn:
        @staticmethod
        def run(app: object, **kwargs: object) -> None:
            started.append(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", _FakeUvicorn)
    result = CliRunner().invoke(cli_module.app, args, catch_exceptions=False)
    return result, started


def test_serving_binds_the_requested_loopback_port(monkeypatch):
    result, started = _serve(monkeypatch, ["--port", "8791", "--no-open"])
    assert result.exit_code == 0
    assert started[-1]["host"] == "127.0.0.1"
    assert started[-1]["port"] == 8791
    assert "serving on this machine only" in result.output


def test_the_printed_link_carries_the_token_warning(monkeypatch):
    result, _ = _serve(monkeypatch, ["--port", "8792", "--no-open"])
    assert "session token" in result.output


def test_a_port_already_in_use_is_reported_rather_than_traced(monkeypatch):
    import tieout_ui.cli as cli_module
    from tieout.cli import EXIT_ERROR

    monkeypatch.setattr(cli_module, "_in_use", lambda host, port: True)
    result, started = _serve(monkeypatch, ["--port", "8793", "--no-open"])
    assert result.exit_code == EXIT_ERROR
    assert "already in use" in result.output
    assert started == [], "nothing should have been bound"


def test_the_session_directory_is_removed_when_the_server_stops(monkeypatch):
    """A deck must not outlive the process that was asked to look at it."""
    from pathlib import Path

    roots: list[Path] = []

    class _Tracked(SessionStore):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__()
            roots.append(self.root)

    monkeypatch.setattr("tieout_ui.session.SessionStore", _Tracked)
    result, _ = _serve(monkeypatch, ["--port", "8794", "--no-open"])
    assert result.exit_code == 0
    assert roots, "the serve command should have created a session store"
    for root in roots:
        assert not root.exists()
