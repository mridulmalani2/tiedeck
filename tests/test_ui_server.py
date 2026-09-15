"""The local server, driven through the app rather than a socket.

Three things are worth testing here and the rest is plumbing: that the token
actually gates everything, that the bind address cannot be widened, and that no
API key is ever retained or echoed. Everything else in this package is a view
over functions already tested elsewhere, and the tests reflect that.
"""

from __future__ import annotations

import json
from typing import Any

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
def dirty_uploaded(client, dirty_path):
    """The seeded deck. The clean one has nothing to correct, by design."""
    response = client.post(
        "/api/decks",
        files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)},
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


def test_running_the_ui_without_its_extra_names_the_extra(monkeypatch):
    """A core-only install that runs `tieout-ui` deserves the one line that
    fixes it, not a traceback through fastapi's import list."""
    import builtins

    from typer.testing import CliRunner

    from tieout.cli import EXIT_ERROR
    from tieout_ui.cli import app

    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith(("fastapi", "tieout_ui.server")):
            raise ImportError(f"No module named {name!r}", name="fastapi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    result = CliRunner().invoke(app, ["--no-open"], catch_exceptions=False)
    assert result.exit_code == EXIT_ERROR
    assert "tieout[ui]" in result.output
    assert "unaffected" in result.output


# --------------------------------------------------------------------------- #
# Dropping a fact, per field kind
# --------------------------------------------------------------------------- #


def test_dropping_a_required_scalar_resets_it_rather_than_crashing(
    client, onboarded, tmp_path
):
    """The schema validates on assignment, so writing None to a required field
    raised a ValidationError out of the handler — a 500 where the UI had invited
    the click. A tolerance is a parameter, not a claim about the client, so
    dropping it means going back to the default."""
    from tieout.profile.schema import BrandProfile

    default = BrandProfile().palette_tolerance_delta_e
    response = client.post(
        "/api/confirm",
        json={"client": "demo", "dropped": ["brand.palette_tolerance_delta_e"]},
    )
    assert response.status_code == 200
    assert response.json()["dropped"] == ["brand.palette_tolerance_delta_e"]

    from tieout.profile.loader import load

    profile = load(tmp_path / "profiles" / "demo.yaml")
    assert profile.brand.palette_tolerance_delta_e == default


def test_dropping_an_optional_field_clears_it(client, onboarded, tmp_path):
    from tieout.profile.loader import load

    client.post("/api/confirm", json={"client": "demo", "dropped": ["typography.quotes"]})
    assert load(tmp_path / "profiles" / "demo.yaml").typography.quotes is None


def test_a_required_field_with_no_default_cannot_be_dropped(client, onboarded, tmp_path):
    """Refused rather than raised, and the profile is left loadable."""
    from tieout.profile.loader import load

    response = client.post(
        "/api/confirm", json={"client": "demo", "dropped": ["slide.width_pt"]}
    )
    assert response.status_code == 200
    assert response.json()["dropped"] == []
    assert load(tmp_path / "profiles" / "demo.yaml").slide.width_pt > 0


def test_the_view_says_which_facts_can_be_dropped(onboarded):
    """So the page only offers the control where it means something. A checkbox
    that does nothing is worse than no checkbox."""
    facts = [fact for group in onboarded["groups"] for fact in group["facts"]]
    assert facts
    assert all("droppable" in fact for fact in facts)
    assert any(fact["droppable"] for fact in facts)


def test_droppability_is_a_dry_run_and_does_not_mutate(reference_profile):
    """`can_clear` works on a copy; asking must not answer by doing."""
    from tieout_ui.edit import can_clear

    before = list(reference_profile.brand.palette_hex)
    assert can_clear(reference_profile, "brand.palette_hex")
    assert reference_profile.brand.palette_hex == before


# --------------------------------------------------------------------------- #
# Two decks at once
# --------------------------------------------------------------------------- #


def test_each_deck_keeps_its_own_report(client, onboarded, uploaded, dirty_path):
    """The report used to live in one slot on the app, so checking a second deck
    made the first one's report link answer 404."""
    second = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()
    for deck_id in (uploaded["deck_id"], second["deck_id"]):
        assert (
            client.post("/api/check", json={"deck_id": deck_id, "client": "demo"}).status_code
            == 200
        )
    first = client.get(f"/api/report/{uploaded['deck_id']}")
    other = client.get(f"/api/report/{second['deck_id']}")
    assert first.status_code == 200
    assert other.status_code == 200
    assert first.text != other.text


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #


def test_the_page_hides_hidden_elements(client):
    """A ribbon group sets display:flex, which beats the browser's own rule for
    [hidden] — so the API key field showed with content review switched off,
    which is exactly the wrong affordance and invisible in a diff."""
    assert "[hidden] { display: none !important; }" in client.get("/").text


def test_the_page_presents_findings_as_a_note_not_as_cards(client):
    """The layout is the feature: bankers read decks in PowerPoint, and a review
    note in a task pane is the idiom they already use for marked-up slides."""
    page = client.get("/").text
    for expected in ("Review note", "plainNote", "slide-head", "class=\"item\""):
        assert expected in page, expected


# --------------------------------------------------------------------------- #
# Editing a derived value
# --------------------------------------------------------------------------- #


def test_the_view_offers_a_control_per_editable_member():
    """A margin set is four decisions, not one string. Editing "30 / 42 / 64 /
    42pt" as a single field is how the third number gets a typo nobody sees."""
    from tieout.profile.schema import Margins, Profile, SlideProfile
    from tieout_ui.edit import editable

    profile = Profile(client="acme", slide=SlideProfile(width_pt=960.0, height_pt=540.0))
    profile.layout.safe_margin_pt["content"] = Margins(
        top=30.0, right=42.0, bottom=64.0, left=42.0
    )
    controls = editable(profile, "layout.safe_margin_pt.content")

    assert [c["label"] for c in controls] == ["top", "right", "bottom", "left"]
    assert {c["type"] for c in controls} == {"number"}
    # Prefilled, so a control submitted untouched is never recorded as an edit.
    assert [c["value"] for c in controls] == ["30", "42", "64", "42"]


def test_a_shape_a_form_cannot_carry_is_not_offered():
    """Silence is the honest answer where a control would be a lie. The page
    must not draw an input that cannot take what is typed into it."""
    from tieout.profile.schema import Profile, SlideProfile
    from tieout_ui.edit import editable

    profile = Profile(client="acme", slide=SlideProfile(width_pt=960.0, height_pt=540.0))
    profile.typography.canon_terms = {"EBITDA": ["ebitda"]}
    assert editable(profile, "typography.canon_terms") == []


def test_a_list_of_words_is_not_read_as_a_list_of_numbers():
    """``list[str] is list[str]`` is False -- parameterised generics are not
    interned -- so an identity test silently made every list numeric and the
    typeface field rejected 'Calibri'."""
    from tieout.profile.schema import Profile, SlideProfile
    from tieout_ui.edit import editable

    profile = Profile(client="acme", slide=SlideProfile(width_pt=960.0, height_pt=540.0))
    profile.brand.fonts.allowed = ["Calibri"]
    assert [c["type"] for c in editable(profile, "brand.fonts.allowed")] == ["list"]


def test_an_edit_is_applied_marked_as_hand_set_and_locked():
    from tieout.profile.schema import Margins, Profile, SlideProfile
    from tieout_ui.edit import apply_edits

    profile = Profile(client="acme", slide=SlideProfile(width_pt=960.0, height_pt=540.0))
    profile.layout.safe_margin_pt["content"] = Margins(
        top=30.0, right=42.0, bottom=64.0, left=42.0
    )
    profile.set_provenance("layout.safe_margin_pt.content.top", "measured", "high")

    applied, rejected = apply_edits(profile, {"layout.safe_margin_pt.content.top": "36"})

    assert applied == ["layout.safe_margin_pt.content.top"] and rejected == []
    assert profile.layout.safe_margin_pt["content"].top == 36.0
    # The provenance must stop claiming the deck said so, and the lock must stop
    # a later `learn --add` from quietly measuring over it.
    assert "set by hand" in profile.provenance["layout.safe_margin_pt.content.top"]
    assert "layout.safe_margin_pt.content.top" in profile.locks


def test_one_bad_value_does_not_discard_the_good_ones():
    """Twelve fields go up in one save. Rejecting the form because one of them
    is wrong loses eleven corrections and says nothing useful about any of
    them."""
    from tieout.profile.schema import Profile, SlideProfile
    from tieout_ui.edit import apply_edits

    profile = Profile(client="acme", slide=SlideProfile(width_pt=960.0, height_pt=540.0))
    applied, rejected = apply_edits(
        profile,
        {
            "brand.palette_tolerance_delta_e": "4.5",
            "brand.palette_hex": "#1F3864, octarine",
            "layout.grid.tolerance_pt": "not a number",
            "typography.nonexistent": "x",
        },
    )

    assert applied == ["brand.palette_tolerance_delta_e"]
    assert profile.brand.palette_tolerance_delta_e == 4.5
    reasons = {entry["path"]: entry["reason"] for entry in rejected}
    assert "not a number" in reasons["layout.grid.tolerance_pt"]
    assert "sRGB" in reasons["brand.palette_hex"]
    assert reasons["typography.nonexistent"] == "no such field in this profile"


def test_a_list_path_that_is_not_an_index_is_a_miss_not_a_crash():
    """``layout.recurring`` is a list whose members the view names by key, so
    ``layout.recurring.name:footnote`` reaches the resolver as a non-numeric
    segment under a list. It used to raise ValueError out of the request."""
    from tieout.profile.schema import Profile, SlideProfile
    from tieout_ui.edit import apply_edits, editable

    profile = Profile(client="acme", slide=SlideProfile(width_pt=960.0, height_pt=540.0))
    assert editable(profile, "layout.recurring.name:footnote") == []
    applied, rejected = apply_edits(profile, {"layout.recurring.name:footnote": "1"})
    assert applied == [] and rejected[0]["reason"] == "no such field in this profile"


def test_the_house_style_is_derived_on_upload_without_being_asked(client, uploaded):
    """The House style tab must have an answer in it by the time anyone opens
    it, and nothing may be written until a person names it."""
    import time

    deadline = time.time() + 120
    while time.time() < deadline:
        payload = client.get(f"/api/decks/{uploaded['deck_id']}/draft").json()
        if payload.get("ready"):
            break
        time.sleep(0.2)

    assert payload["ready"] is True and payload["draft"] is True
    assert payload["groups"], "the draft must carry the same facts a profile does"
    assert client.get("/api/profiles").json()["clients"] == [], (
        "a draft is a candidate, not a profile: nothing is written until it is named"
    )


def test_an_edit_survives_the_round_trip_through_confirm(client, onboarded):
    response = client.post(
        "/api/confirm",
        json={
            "client": "demo",
            "edits": {"brand.palette_tolerance_delta_e": "4.5"},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["edited"] == ["brand.palette_tolerance_delta_e"]
    assert payload["rejected"] == []

    reloaded = client.get("/api/profiles/demo").json()
    tolerance = next(
        fact
        for group in reloaded["groups"]
        for fact in group["facts"]
        if fact["path"] == "brand.palette_tolerance_delta_e"
    )
    assert tolerance["value"] == 4.5
    assert "set by hand" in tolerance["why"]


def test_a_refused_edit_is_reported_rather_than_raised(client, onboarded):
    response = client.post(
        "/api/confirm",
        json={"client": "demo", "edits": {"brand.palette_hex": "#1F3864, octarine"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["edited"] == []
    assert "sRGB" in response.json()["rejected"][0]["reason"]


# --------------------------------------------------------------------------- #
# The review note as advice rather than a log
# --------------------------------------------------------------------------- #


def _audited(deck, profile):
    from tieout.rules.base import clear_caches, run_rules
    from tieout_ui.view import audit_view

    clear_caches()
    return audit_view(run_rules(deck, profile), deck)


def test_the_note_opens_with_a_send_or_do_not_send_call(dirty_deck, reference_profile):
    """"47 points to address" is a count, not an answer. The question anyone
    opens this with is whether the deck can go out."""
    view = _audited(dirty_deck, reference_profile)
    assert view["verdict"]["state"] == "block"
    assert view["verdict"]["headline"] == "Not ready to send"


def test_a_clean_deck_is_told_it_is_clean(clean_deck, reference_profile):
    view = _audited(clean_deck, reference_profile)
    assert view["verdict"]["state"] == "clear"
    assert view["verdict"]["headline"] == "Ready to send"
    assert view["actions"] == []


def test_findings_collapse_to_the_decisions_behind_them(dirty_deck, reference_profile):
    """One off-palette colour on four slides is one job. Listed slide by slide
    it reads as four problems, and a reader who fixes the theme colour once has
    to work out that the other three were the same thing."""
    view = _audited(dirty_deck, reference_profile)
    actions = view["actions"]

    assert actions, "a deck with findings has things to do"
    assert len(actions) < view["summary"]["total"], (
        "grouping that collapses nothing is not grouping"
    )
    assert sum(action["count"] for action in actions) == view["summary"]["total"], (
        "every finding belongs to exactly one action, or the note loses some"
    )


def test_the_worst_thing_to_do_is_first(dirty_deck, reference_profile):
    from tieout.rules.base import SEVERITY_ORDER

    actions = _audited(dirty_deck, reference_profile)["actions"]
    ranks = [SEVERITY_ORDER[action["severity"]] for action in actions]
    assert ranks == sorted(ranks), "the list is the order to work through"


def test_a_group_states_no_measurement_its_members_disagree_on(
    dirty_deck, reference_profile
):
    """Six shapes off six different grid lines have six expectations. Printing
    the first one on the group header is a wrong number stated confidently."""
    for action in _audited(dirty_deck, reference_profile)["actions"]:
        for field in ("measured", "expected"):
            values = {instance[field] for instance in action["instances"]}
            if len(values) > 1:
                assert action[field] is None, (
                    f"{action['rule_id']} states one {field} for instances that differ"
                )


def test_a_finding_says_what_to_do_about_it(dirty_deck, reference_profile):
    """A Delta-E figure is a diagnosis. The tool has already worked out the
    nearest palette colour; not saying it leaves the reader to do it again."""
    view = _audited(dirty_deck, reference_profile)
    findings = [f for slide in view["slides"] for f in slide["findings"]]

    assert findings
    without = sorted({f["rule_id"] for f in findings if not f["remedy"]})
    assert not without, f"these rules report a defect without a fix: {without}"


def test_a_remedy_never_shows_the_reader_a_format_code(dirty_deck, reference_profile):
    """``%d %B %Y`` is a correct answer to the wrong question: nobody reformats
    a deck from a strftime string."""
    view = _audited(dirty_deck, reference_profile)
    for slide in view["slides"]:
        for finding in slide["findings"]:
            if finding["rule_id"] == "TY-008":
                assert "%" not in (finding["remedy"] or "")


def test_every_finding_that_is_about_a_shape_can_be_pointed_at(
    dirty_deck, reference_profile
):
    """The box has been carried on every finding since the first version and
    nothing drew it. The page needs it to outline the shape on the canvas."""
    view = _audited(dirty_deck, reference_profile)
    shaped = [
        finding
        for slide in view["slides"]
        for finding in slide["findings"]
        if finding["shape"]
    ]
    assert shaped
    assert all(f["bbox_pt"] and len(f["bbox_pt"]) == 4 for f in shaped), (
        "a finding that names a shape must say where that shape is"
    )


# --------------------------------------------------------------------------- #
# Correcting the deck
# --------------------------------------------------------------------------- #


def _check(client, deck_id, name="demo"):
    response = client.post("/api/check", json={"deck_id": deck_id, "client": name})
    assert response.status_code == 200, response.text
    return response.json()


def test_an_action_says_whether_it_can_be_corrected(client, dirty_uploaded, onboarded):
    view = _check(client, dirty_uploaded["deck_id"])
    assert view["actions"], "the reference deck has findings to act on"
    assert all("fixable" in action for action in view["actions"])
    assert view["can_undo"] is False and view["edited"] is False


def test_applying_a_correction_reduces_the_findings_and_can_be_undone(
    client, dirty_uploaded, onboarded
):
    view = _check(client, dirty_uploaded["deck_id"])
    fixable = [a for a in view["actions"] if a["fixable"]]
    if not fixable:
        pytest.skip("this deck has nothing mechanically correctable")

    before = view["summary"]["total"]
    fixed = client.post(
        "/api/fix",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo", "key": fixable[0]["key"]},
    )
    assert fixed.status_code == 200, fixed.text
    after = fixed.json()
    assert after["summary"]["total"] < before
    assert after["edited"] is True and after["can_undo"] is True
    assert after["applied"] == [fixable[0]["remedy"]] or after["fixed"]

    undone = client.post(
        "/api/undo", json={"deck_id": dirty_uploaded["deck_id"], "client": "demo"}
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["summary"]["total"] == before
    assert undone.json()["can_undo"] is False


def test_undo_with_nothing_to_undo_is_refused_rather_than_guessed_at(
    client, dirty_uploaded, onboarded
):
    _check(client, dirty_uploaded["deck_id"])
    response = client.post(
        "/api/undo", json={"deck_id": dirty_uploaded["deck_id"], "client": "demo"}
    )
    assert response.status_code == 400


def test_a_correction_that_is_not_on_offer_is_refused(client, dirty_uploaded, onboarded):
    _check(client, dirty_uploaded["deck_id"])
    response = client.post(
        "/api/fix",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo", "key": "LO-003|invented"},
    )
    assert response.status_code == 400


def test_setting_one_aside_marks_it_and_puts_it_back(client, dirty_uploaded, onboarded):
    view = _check(client, dirty_uploaded["deck_id"])
    key = view["actions"][0]["key"]

    aside = client.post(
        "/api/reject",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo", "key": key,
              "rejected": True},
    ).json()
    assert next(a for a in aside["actions"] if a["key"] == key)["rejected"] is True

    back = client.post(
        "/api/reject",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo", "key": key,
              "rejected": False},
    ).json()
    assert next(a for a in back["actions"] if a["key"] == key)["rejected"] is False


def test_setting_one_aside_never_reaches_the_profile(client, dirty_uploaded, onboarded):
    """Deciding to leave one deck's colour alone is not a decision about the
    client's house style, and writing it to the profile would make it one."""
    view = _check(client, dirty_uploaded["deck_id"])
    before = client.get("/api/profiles/demo").json()

    client.post(
        "/api/reject",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo",
              "key": view["actions"][0]["key"], "rejected": True},
    )
    assert client.get("/api/profiles/demo").json() == before


def test_the_export_is_a_readable_deck(client, dirty_uploaded, onboarded):
    import io
    import zipfile

    _check(client, dirty_uploaded["deck_id"])
    response = client.get(f"/api/export/{dirty_uploaded['deck_id']}")
    assert response.status_code == 200
    assert "presentationml" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert "ppt/presentation.xml" in archive.namelist()


def test_the_export_is_named_as_corrected_only_once_it_is(client, dirty_uploaded, onboarded):
    view = _check(client, dirty_uploaded["deck_id"])
    plain = client.get(f"/api/export/{dirty_uploaded['deck_id']}")
    assert "corrected" not in plain.headers["content-disposition"]

    fixable = [a for a in view["actions"] if a["fixable"]]
    if not fixable:
        pytest.skip("this deck has nothing mechanically correctable")
    client.post(
        "/api/fix",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo", "key": fixable[0]["key"]},
    )
    assert "corrected" in client.get(
        f"/api/export/{dirty_uploaded['deck_id']}"
    ).headers["content-disposition"]


def test_the_upload_itself_is_never_written_over(client, dirty_uploaded, onboarded, store):
    """Undo is putting a path back, which only works while the earlier file is
    still the earlier file."""
    deck = store.require(dirty_uploaded["deck_id"])
    original = deck.path.read_bytes()

    view = _check(client, dirty_uploaded["deck_id"])
    fixable = [a for a in view["actions"] if a["fixable"]]
    if not fixable:
        pytest.skip("this deck has nothing mechanically correctable")
    client.post(
        "/api/fix",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo", "key": fixable[0]["key"]},
    )
    assert deck.path.read_bytes() == original
    assert deck.current != deck.path
