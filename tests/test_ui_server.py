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
from tieout.model.units import pt_to_emu
from tieout.profile.loader import PROFILE_DIR_ENV
from tieout_ui.server import create_app, is_loopback
from tieout_ui.session import MAX_UPLOAD_BYTES, SessionStore
from tieout_ui.view import find_shape, movable


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
    ("GET", "/api/canvas/anything/1"),
    ("GET", "/api/media/anything/1/1"),
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
# The live surface: a slide's shape tree and its pictures' own bytes
# --------------------------------------------------------------------------- #


def test_the_canvas_route_needs_a_client(client, onboarded, uploaded):
    response = client.get(f"/api/canvas/{uploaded['deck_id']}/1")
    assert response.status_code == 422  # a required query param, missing


def test_the_canvas_route_serves_the_shape_tree(client, onboarded, uploaded):
    response = client.get(f"/api/canvas/{uploaded['deck_id']}/1?client=demo")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["width_pt"] > 0 and body["height_pt"] > 0
    assert body["shapes"], "the title slide has shapes"
    assert {"uid", "shape_id", "kind", "left_pt", "editable"} <= set(body["shapes"][0])


def test_the_canvas_route_is_a_404_past_the_last_slide(client, onboarded, uploaded):
    response = client.get(f"/api/canvas/{uploaded['deck_id']}/999?client=demo")
    assert response.status_code == 404


def _pictured_deck(path):
    """One slide carrying a real picture, for the media route to serve back."""
    import io

    from PIL import Image
    from pptx import Presentation
    from pptx.util import Emu, Pt

    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    png = io.BytesIO()
    Image.new("RGB", (40, 20), (10, 20, 30)).save(png, format="PNG")
    png.seek(0)
    slide.shapes.add_picture(png, Pt(60), Pt(80), Pt(200), Pt(100))

    presentation.save(str(path))
    return path


def test_the_media_route_serves_a_pictures_own_bytes(client, onboarded, store, tmp_path):
    import io

    from PIL import Image

    path = _pictured_deck(tmp_path / "pictured.pptx")
    uploaded = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()
    deck = store.require(uploaded["deck_id"])
    picture = next(s for _, s in deck.model.all_shapes() if s.kind == "picture")

    response = client.get(f"/api/media/{uploaded['deck_id']}/1/{picture.ref.uid}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content, "the image's own bytes came back"
    assert Image.open(io.BytesIO(response.content)).size == (40, 20)


def test_the_media_route_is_a_404_for_a_shape_with_no_picture(client, onboarded, uploaded):
    response = client.get(f"/api/media/{uploaded['deck_id']}/1/999999")
    assert response.status_code == 404


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
            "approved_digest": None,
        },
    )
    assert response.status_code == 200
    review = response.json()["review"]
    assert review["held"] is True
    assert calls == [], "the transport must not have been reached"
    assert "Nothing has been sent" in review["detail"]


# --------------------------------------------------------------------------- #
# The approval binding
# --------------------------------------------------------------------------- #
#
# The gap this closes, in the words of PLAN.md §4: "``approved`` is an unbound
# assertion. The browser posts ``approved: true``; the server cannot tell
# whether that approval corresponds to the residual list it displayed, or to any
# list at all." Over HTTP the display and the send are two requests, and
# anything can happen between them. These are the tests that the second request
# has to name what the first one showed.


class _Recording:
    """A transport that remembers whether it was reached at all."""

    model = "stub-model"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, schema):
        self.calls.append((system, user))
        return json.dumps({"findings": []}), {"input_tokens": 1, "output_tokens": 1}


def _transport(monkeypatch) -> _Recording:
    import tieout_ui.server as server_module

    recording = _Recording()
    monkeypatch.setattr(server_module, "_build_review_client", lambda *a, **k: recording)
    return recording


def _preview(client, deck_id, **extra: object):
    body = {"deck_id": deck_id, "client": "demo"}
    body.update(extra)
    return client.post("/api/redact", json=body).json()


def test_the_redaction_plan_carries_a_digest(client, onboarded, uploaded):
    plan = _preview(client, uploaded["deck_id"])
    assert len(plan["digest"]) == 64
    assert plan["digest"] == _preview(client, uploaded["deck_id"])["digest"], (
        "the same deck and the same terms must produce the same digest, or the "
        "binding would refuse every honest approval"
    )


def test_a_digest_from_a_different_term_list_is_refused(
    client, onboarded, dirty_path, monkeypatch
):
    """The realistic drift: the forbidden-terms box is edited after previewing.

    Two requests, and between them the payload changed. The approval still names
    the old one, and the old one is not what would be sent.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    transport = _transport(monkeypatch)
    upload = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()

    preview = _preview(client, upload["deck_id"], forbidden="Ashcombe Partners")
    assert preview["residuals"], "the dirty fixture is expected to leave a residual"

    review = client.post(
        "/api/check",
        json={
            "deck_id": upload["deck_id"],
            "client": "demo",
            "semantic": True,
            # The approval is honest; the term list underneath it is not the one
            # it was granted for.
            "forbidden": "",
            "approved_digest": preview["digest"],
        },
    ).json()["review"]

    assert review["held"] is True
    assert "the deck changed since you approved this" in review["detail"]
    assert transport.calls == [], "a refused approval must not have reached the transport"


def test_a_digest_that_matches_sends(client, onboarded, dirty_path, monkeypatch):
    """The other half. A binding that refused everything would also pass the
    test above, and would be useless."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    transport = _transport(monkeypatch)
    upload = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()

    preview = _preview(client, upload["deck_id"], forbidden="Ashcombe Partners")
    review = client.post(
        "/api/check",
        json={
            "deck_id": upload["deck_id"],
            "client": "demo",
            "semantic": True,
            "forbidden": "Ashcombe Partners",
            "approved_digest": preview["digest"],
        },
    ).json()["review"]

    assert review["held"] is False, review.get("detail")
    assert len(transport.calls) == 1


def test_a_bare_true_is_no_longer_an_approval(client, onboarded, dirty_path, monkeypatch):
    """The field that used to carry the claim is gone, and a client still
    sending it is refused rather than quietly believed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    transport = _transport(monkeypatch)
    upload = client.post(
        "/api/decks", files={"file": (dirty_path.name, dirty_path.read_bytes(), _MIME)}
    ).json()
    assert _preview(client, upload["deck_id"])["residuals"]

    review = client.post(
        "/api/check",
        json={
            "deck_id": upload["deck_id"],
            "client": "demo",
            "semantic": True,
            "approved": True,
        },
    ).json()["review"]

    assert review["held"] is True
    assert transport.calls == []


def test_the_page_echoes_the_digest_rather_than_a_boolean(client):
    """Asserted on the served page, because a server that binds and a page that
    never sends the binding is a hold nobody can satisfy."""
    page = client.get("/").text
    assert "approved_digest" in page
    assert "S.digest = plan.digest" in page


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
    # ``class="item`` rather than the closed attribute: a finding the last
    # correction exposed carries a second class beside it.
    for expected in ("Review note", "plainNote", "slide-head", "class=\"item"):
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


# --------------------------------------------------------------------------- #
# Moving a shape
#
# The one route that writes geometry, and the reason it may is that it decides
# none of it. What is worth testing is that the number the person supplied is
# the number written, that the move flows through the same correction machinery
# as everything else so undo and export need no special case, and that the
# refusals around it hold.
# --------------------------------------------------------------------------- #


def _at(model, slide_index, shape_id):
    """Where a shape actually sits in a deck, in whole EMU."""
    shape = find_shape(model, slide_index, shape_id)
    assert shape is not None, f"slide {slide_index} has no shape {shape_id}"
    left, top = pt_to_emu(shape.left_pt), pt_to_emu(shape.top_pt)
    assert left is not None and top is not None
    return (left, top)


def _movable(view):
    """The first finding in an audit whose shape this can pick up."""
    for slide in view["slides"]:
        for finding in slide["findings"]:
            move = finding["move"]
            if move and not move["refused"] and finding["rule_id"] != "BR-009":
                return slide["index"], move
    return None, None


def test_the_audit_carries_the_grid_a_shape_can_be_snapped_to(
    client, dirty_uploaded, onboarded
):
    """The same grid LO-003 measures against, so a shape dragged onto a line
    clears the finding that named it rather than landing merely near it."""
    view = _check(client, dirty_uploaded["deck_id"])
    grid = view["grid"]
    assert set(grid) == {"columns_pt", "rows_pt", "tolerance_pt", "snap_pt", "why"}
    assert grid["columns_pt"] or grid["rows_pt"]
    assert grid["tolerance_pt"] > 0


def test_the_magnet_reaches_the_shapes_the_rule_reports(client, dirty_uploaded, onboarded):
    """The property that makes snapping useful rather than decorative.

    LO-003 reports an edge further from a line than ``grid.tolerance_pt`` -- that
    is what makes it off-grid -- and no further than the near-miss window. A
    magnet the width of ``tolerance_pt`` could not reach a single one of them, so
    the reach is the window instead.
    """
    grid = _check(client, dirty_uploaded["deck_id"])["grid"]
    assert grid["snap_pt"] > grid["tolerance_pt"], (
        "a magnet no wider than the on-grid tolerance can never reach a near miss"
    )


def test_a_finding_about_a_shape_says_how_to_pick_it_up(client, dirty_uploaded, onboarded):
    view = _check(client, dirty_uploaded["deck_id"])
    _, move = _movable(view)
    assert move is not None, "the seeded deck reports shapes"
    assert {"shape_id", "shape", "x_emu", "y_emu", "cx_emu", "cy_emu"} <= set(move)
    # EMU, as integers: the page's arithmetic has to be exact -- see move_fix.
    for key in ("x_emu", "y_emu", "cx_emu", "cy_emu"):
        assert isinstance(move[key], int)


def test_moving_a_shape_writes_the_offset_and_can_be_undone(
    client, dirty_uploaded, onboarded, store
):
    deck_id = dirty_uploaded["deck_id"]
    view = _check(client, deck_id)
    slide_index, move = _movable(view)
    assert move is not None

    target = (move["x_emu"] + 18 * 12700, move["y_emu"] - 6 * 12700)
    moved = client.post(
        "/api/move",
        json={
            "deck_id": deck_id, "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "x_emu": target[0], "y_emu": target[1],
        },
    )
    assert moved.status_code == 200, moved.text
    after = moved.json()
    assert after["edited"] is True and after["can_undo"] is True
    assert after["moved"].startswith("Move ")
    assert after["applied"] == [after["moved"]]

    # The deck itself, not just the payload.
    assert _at(store.require(deck_id).model, slide_index, move["shape_id"]) == target

    undone = client.post("/api/undo", json={"deck_id": deck_id, "client": "demo"})
    assert undone.status_code == 200, undone.text
    assert undone.json()["can_undo"] is False
    assert _at(store.require(deck_id).model, slide_index, move["shape_id"]) == (
        move["x_emu"], move["y_emu"],
    )


def test_a_moved_deck_exports_with_the_shape_moved(client, dirty_uploaded, onboarded, tmp_path):
    """The whole point of the correction machinery being shared: export needed
    no change to carry a move."""
    deck_id = dirty_uploaded["deck_id"]
    view = _check(client, deck_id)
    slide_index, move = _movable(view)
    assert move is not None
    target = (move["x_emu"] + 24 * 12700, move["y_emu"])

    assert client.post(
        "/api/move",
        json={
            "deck_id": deck_id, "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "x_emu": target[0], "y_emu": target[1],
        },
    ).status_code == 200

    exported = client.get(f"/api/export/{deck_id}")
    assert exported.status_code == 200
    assert "corrected" in exported.headers["content-disposition"]
    out = tmp_path / "exported.pptx"
    out.write_bytes(exported.content)
    assert _at(load_deck(out), slide_index, move["shape_id"]) == target


def test_snapping_a_near_miss_onto_its_grid_line_clears_the_finding(
    client, dirty_uploaded, onboarded, store
):
    """The acceptance criterion the whole feature exists for.

    LO-003 reports a shape edge sitting just off a learned grid line. Put the
    edge on the line -- which is what dragging with snap on does -- and the
    finding is gone on the next check, because the editor aims at the same grid
    the rule measures against.
    """
    deck_id = dirty_uploaded["deck_id"]
    view = _check(client, deck_id)
    grid = view["grid"]

    candidate = None
    for slide in view["slides"]:
        for finding in slide["findings"]:
            move = finding["move"]
            if finding["rule_id"] == "LO-003" and move and not move["refused"]:
                candidate = (slide["index"], move)
                break
        if candidate:
            break
    assert candidate is not None, "the seeded deck reports a movable near miss"

    slide_index, move = candidate
    before = sum(
        1 for s in view["slides"] for f in s["findings"]
        if f["rule_id"] == "LO-003" and (f["move"] or {}).get("shape_id") == move["shape_id"]
    )
    assert before

    # Snap exactly as the page does: the nearer of the two edges, against the
    # columns for x and the rows for y.
    def snapped(value, size, lines):
        best = value
        off = None
        for line in lines:
            at = round(line * 12700)
            for candidate_value in (at, at - size):
                delta = abs(candidate_value - value)
                if delta <= grid["snap_pt"] * 12700 and (off is None or delta < off):
                    best, off = candidate_value, delta
        return best

    x = snapped(move["x_emu"], move["cx_emu"], grid["columns_pt"])
    y = snapped(move["y_emu"], move["cy_emu"], grid["rows_pt"])
    assert (x, y) != (move["x_emu"], move["y_emu"]), (
        "a near miss has to be within the magnet's reach, or snapping cannot "
        "clear the finding that reported it"
    )

    moved = client.post(
        "/api/move",
        json={
            "deck_id": deck_id, "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "x_emu": x, "y_emu": y,
        },
    )
    assert moved.status_code == 200, moved.text
    after = sum(
        1 for s in moved.json()["slides"] for f in s["findings"]
        if f["rule_id"] == "LO-003" and (f["move"] or {}).get("shape_id") == move["shape_id"]
    )
    assert after < before, "snapping to the learned line should clear the near miss"


def _grouped_deck(path):
    """A deck with one shape inside a group, at 1:1 scale, and one beside it."""
    from pptx import Presentation
    from pptx.util import Emu, Pt

    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    loose = slide.shapes.add_textbox(Pt(60), Pt(80), Pt(300), Pt(40))
    loose.name = "Loose"
    loose.text_frame.text = "Not in a group."
    group = slide.shapes.add_group_shape()
    group.name = "Group"
    inner = group.shapes.add_textbox(Pt(400), Pt(200), Pt(200), Pt(40))
    inner.name = "Inside"
    inner.text_frame.text = "In a group."
    presentation.save(str(path))
    return path


def _scaled_grouped_deck(path):
    """A group whose box was resized after the fact, the way dragging a
    group's own corner handle in PowerPoint does: ``a:ext`` changes and
    ``a:chExt`` does not, so its child's coordinates are no longer 1:1 with
    the slide's. Doubled on both axes here, chosen so a scale bug shows up as
    a wrong answer rather than as a coincidentally correct one."""
    from pptx import Presentation
    from pptx.util import Emu, Pt

    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    group.name = "Group"
    inner = group.shapes.add_textbox(Pt(100), Pt(100), Pt(100), Pt(40))
    inner.name = "Inside"
    inner.text_frame.text = "Scaled."
    # chOff/chExt freeze at (100, 100, 100, 40)pt here; only off/ext change.
    group.left, group.top, group.width, group.height = Pt(150), Pt(150), Pt(200), Pt(80)
    presentation.save(str(path))
    return path


def _rotated_grouped_deck(path):
    """A group that also rotates its children -- the one case this feature
    refuses rather than writing into, because a rotation is not a transform
    "un-scaling" alone can invert."""
    from pptx import Presentation
    from pptx.util import Emu, Pt

    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    group.name = "Group"
    inner = group.shapes.add_textbox(Pt(400), Pt(200), Pt(200), Pt(40))
    inner.name = "Inside"
    inner.text_frame.text = "Rotated group."
    group.rotation = 30.0
    presentation.save(str(path))
    return path


def test_a_shape_inside_a_plain_group_moves_in_its_own_coordinate_space(
    client, onboarded, store, tmp_path
):
    """The group here is 1:1 -- its own box was never resized after the fact
    -- so the shape's own coordinate space and slide space agree, and the
    write lands at exactly the slide-space target requested."""
    path = _grouped_deck(tmp_path / "grouped.pptx")
    uploaded = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()
    deck = store.require(uploaded["deck_id"])

    inside = next(
        shape
        for _, shape in deck.model.all_shapes()
        if shape.ref.name == "Inside"
    )
    assert inside.ref.group_path, "the fixture puts this shape inside a group"

    target = (90 * 12700, 60 * 12700)
    response = client.post(
        "/api/move",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": inside.ref.shape_id, "x_emu": target[0], "y_emu": target[1],
        },
    )
    assert response.status_code == 200, response.text
    assert _at(store.require(uploaded["deck_id"]).model, 1, inside.ref.shape_id) == target

    # And the shape beside it still moves too, independently.
    loose = next(
        shape for _, shape in deck.model.all_shapes() if shape.ref.name == "Loose"
    )
    moved = client.post(
        "/api/move",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": loose.ref.shape_id,
            "x_emu": 90 * 12700, "y_emu": 90 * 12700,
        },
    )
    assert moved.status_code == 200, moved.text


def test_a_shape_inside_a_scaled_group_moves_by_the_unscaled_amount(
    client, onboarded, store, tmp_path
):
    """The group here doubled its own box without touching its children's
    coordinate space, so its child space is half of slide space on both
    axes. Moving the shape 40pt right and 20pt down in slide space has to
    write 20pt and 10pt into the shape's own offset -- half, not the raw
    slide-space amount -- or the shape lands at twice the requested
    distance."""
    path = _scaled_grouped_deck(tmp_path / "scaled.pptx")
    uploaded = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()
    deck = store.require(uploaded["deck_id"])
    inside = next(s for _, s in deck.model.all_shapes() if s.ref.name == "Inside")
    before = _at(deck.model, 1, inside.ref.shape_id)

    target = (before[0] + 40 * 12700, before[1] + 20 * 12700)
    response = client.post(
        "/api/move",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": inside.ref.shape_id, "x_emu": target[0], "y_emu": target[1],
        },
    )
    assert response.status_code == 200, response.text
    assert _at(store.require(uploaded["deck_id"]).model, 1, inside.ref.shape_id) == target


def test_a_shape_inside_a_rotated_group_is_refused_for_move_and_resize(
    client, onboarded, store, tmp_path
):
    """The one case this feature does not attempt: a group that also rotates
    or mirrors its children is a transform "un-scaling" alone cannot invert,
    and guessing at it is exactly what this module refuses to do anywhere
    else."""
    path = _rotated_grouped_deck(tmp_path / "rotated.pptx")
    uploaded = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()
    deck = store.require(uploaded["deck_id"])
    inside = next(s for _, s in deck.model.all_shapes() if s.ref.name == "Inside")

    moved = client.post(
        "/api/move",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": inside.ref.shape_id, "x_emu": 0, "y_emu": 0,
        },
    )
    assert moved.status_code == 422
    assert "rotat" in moved.json()["detail"]

    resized = client.post(
        "/api/resize",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": inside.ref.shape_id, "cx_emu": 12700, "cy_emu": 12700,
        },
    )
    assert resized.status_code == 422
    assert "rotat" in resized.json()["detail"]


def test_a_grouped_shape_reports_movable_unless_its_group_rotates(store, tmp_path):
    """The page has to be able to say *why* rather than just not offering, or a
    shape that cannot be dragged looks like a shape the tool failed to notice."""
    plain = load_deck(_grouped_deck(tmp_path / "grouped.pptx"))
    inside = next(s for _, s in plain.all_shapes() if s.ref.name == "Inside")
    loose = next(s for _, s in plain.all_shapes() if s.ref.name == "Loose")
    assert movable(inside) == ""
    assert movable(loose) == ""

    rotated = load_deck(_rotated_grouped_deck(tmp_path / "rotated.pptx"))
    rotated_inside = next(s for _, s in rotated.all_shapes() if s.ref.name == "Inside")
    assert "rotat" in movable(rotated_inside)


def test_moving_a_shape_that_is_not_there_is_refused(client, dirty_uploaded, onboarded):
    _check(client, dirty_uploaded["deck_id"])
    response = client.post(
        "/api/move",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": 999_999, "x_emu": 0, "y_emu": 0,
        },
    )
    assert response.status_code == 404


def test_a_position_nothing_could_be_placed_at_is_refused(client, dirty_uploaded, onboarded):
    """Parking a shape off the slide is the person's business and LO-001 reports
    it. A coordinate a thousand slides away is a malformed request, and writing
    it would produce a file PowerPoint will not open."""
    view = _check(client, dirty_uploaded["deck_id"])
    slide_index, move = _movable(view)
    assert move is not None
    response = client.post(
        "/api/move",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "x_emu": 99_999_999_999, "y_emu": 0,
        },
    )
    assert response.status_code == 422


def test_moving_a_shape_to_where_it_already_is_writes_no_version(
    client, dirty_uploaded, onboarded, store
):
    """Otherwise a click that changed nothing leaves an undo step that undoes
    nothing, and the count of corrections made stops meaning anything."""
    view = _check(client, dirty_uploaded["deck_id"])
    slide_index, move = _movable(view)
    assert move is not None
    response = client.post(
        "/api/move",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "x_emu": move["x_emu"], "y_emu": move["y_emu"],
        },
    )
    assert response.status_code == 400
    assert not store.require(dirty_uploaded["deck_id"]).history


def test_moving_needs_the_token_like_everything_else(store, tmp_path, monkeypatch):
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "profiles"))
    with TestClient(create_app(store)) as anonymous:
        response = anonymous.post(
            "/api/move",
            json={"deck_id": "x", "client": "demo", "slide": 1, "shape_id": 1,
                  "x_emu": 0, "y_emu": 0},
        )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Resizing a shape -- the mirror of moving one
#
# Every property tested above for /api/move, tested again for /api/resize: it
# writes exactly the size it was given, undo and export carry it, a grouped
# shape is refused with the reason, and the sanity checks hold. The two
# features share one implementation underneath (tieout_fix._transform), so a
# regression that broke only one of them would say something about which half
# changed rather than about resizing itself.
# --------------------------------------------------------------------------- #


def _size_at(model, slide_index, shape_id):
    """Where a shape's size actually is in a deck, in whole EMU."""
    shape = find_shape(model, slide_index, shape_id)
    assert shape is not None, f"slide {slide_index} has no shape {shape_id}"
    cx, cy = pt_to_emu(shape.width_pt), pt_to_emu(shape.height_pt)
    assert cx is not None and cy is not None
    return (cx, cy)


def test_resizing_a_shape_writes_the_extent_and_can_be_undone(
    client, dirty_uploaded, onboarded, store
):
    deck_id = dirty_uploaded["deck_id"]
    view = _check(client, deck_id)
    slide_index, move = _movable(view)
    assert move is not None

    target = (move["cx_emu"] + 30 * 12700, move["cy_emu"] + 12 * 12700)
    resized = client.post(
        "/api/resize",
        json={
            "deck_id": deck_id, "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "cx_emu": target[0], "cy_emu": target[1],
        },
    )
    assert resized.status_code == 200, resized.text
    after = resized.json()
    assert after["edited"] is True and after["can_undo"] is True
    assert after["resized"].startswith("Resize ")
    assert after["applied"] == [after["resized"]]

    assert _size_at(store.require(deck_id).model, slide_index, move["shape_id"]) == target
    # The position is untouched -- a resize is not a move.
    assert _at(store.require(deck_id).model, slide_index, move["shape_id"]) == (
        move["x_emu"], move["y_emu"],
    )

    undone = client.post("/api/undo", json={"deck_id": deck_id, "client": "demo"})
    assert undone.status_code == 200, undone.text
    assert undone.json()["can_undo"] is False
    assert _size_at(store.require(deck_id).model, slide_index, move["shape_id"]) == (
        move["cx_emu"], move["cy_emu"],
    )


def test_a_resized_deck_exports_with_the_shape_resized(
    client, dirty_uploaded, onboarded, tmp_path
):
    deck_id = dirty_uploaded["deck_id"]
    view = _check(client, deck_id)
    slide_index, move = _movable(view)
    assert move is not None
    target = (move["cx_emu"] + 40 * 12700, move["cy_emu"])

    assert client.post(
        "/api/resize",
        json={
            "deck_id": deck_id, "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "cx_emu": target[0], "cy_emu": target[1],
        },
    ).status_code == 200

    exported = client.get(f"/api/export/{deck_id}")
    assert exported.status_code == 200
    out = tmp_path / "exported.pptx"
    out.write_bytes(exported.content)
    assert _size_at(load_deck(out), slide_index, move["shape_id"]) == target


def test_a_shape_inside_a_plain_group_resizes_in_its_own_coordinate_space(
    client, onboarded, store, tmp_path
):
    path = _grouped_deck(tmp_path / "grouped-resize.pptx")
    uploaded = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()
    deck = store.require(uploaded["deck_id"])

    inside = next(
        shape for _, shape in deck.model.all_shapes() if shape.ref.name == "Inside"
    )
    assert inside.ref.group_path, "the fixture puts this shape inside a group"

    target = (240 * 12700, 60 * 12700)
    response = client.post(
        "/api/resize",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": inside.ref.shape_id, "cx_emu": target[0], "cy_emu": target[1],
        },
    )
    assert response.status_code == 200, response.text
    assert _size_at(store.require(uploaded["deck_id"]).model, 1, inside.ref.shape_id) == target

    loose = next(
        shape for _, shape in deck.model.all_shapes() if shape.ref.name == "Loose"
    )
    resized = client.post(
        "/api/resize",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": loose.ref.shape_id,
            "cx_emu": 200 * 12700, "cy_emu": 50 * 12700,
        },
    )
    assert resized.status_code == 200, resized.text


def test_a_shape_inside_a_scaled_group_resizes_by_the_unscaled_amount(
    client, onboarded, store, tmp_path
):
    """The mirror of the equivalent move test: growing the shape by 60pt and
    30pt in slide space has to write 30pt and 15pt into its own extent, given
    the fixture's 2x group scale."""
    path = _scaled_grouped_deck(tmp_path / "scaled-resize.pptx")
    uploaded = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()
    deck = store.require(uploaded["deck_id"])
    inside = next(s for _, s in deck.model.all_shapes() if s.ref.name == "Inside")
    before = _size_at(deck.model, 1, inside.ref.shape_id)

    target = (before[0] + 60 * 12700, before[1] + 30 * 12700)
    response = client.post(
        "/api/resize",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": inside.ref.shape_id, "cx_emu": target[0], "cy_emu": target[1],
        },
    )
    assert response.status_code == 200, response.text
    assert _size_at(store.require(uploaded["deck_id"]).model, 1, inside.ref.shape_id) == target


def test_resizing_a_shape_that_is_not_there_is_refused(client, dirty_uploaded, onboarded):
    _check(client, dirty_uploaded["deck_id"])
    response = client.post(
        "/api/resize",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": 999_999, "cx_emu": 12700, "cy_emu": 12700,
        },
    )
    assert response.status_code == 404


def test_a_size_nothing_could_be_placed_at_is_refused(client, dirty_uploaded, onboarded):
    view = _check(client, dirty_uploaded["deck_id"])
    slide_index, move = _movable(view)
    assert move is not None
    too_big = client.post(
        "/api/resize",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "cx_emu": 99_999_999_999, "cy_emu": 12700,
        },
    )
    assert too_big.status_code == 422

    zero = client.post(
        "/api/resize",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "cx_emu": 0, "cy_emu": 12700,
        },
    )
    assert zero.status_code == 422


def test_resizing_a_shape_to_the_size_it_already_is_writes_no_version(
    client, dirty_uploaded, onboarded, store
):
    view = _check(client, dirty_uploaded["deck_id"])
    slide_index, move = _movable(view)
    assert move is not None
    response = client.post(
        "/api/resize",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": slide_index,
            "shape_id": move["shape_id"], "cx_emu": move["cx_emu"], "cy_emu": move["cy_emu"],
        },
    )
    assert response.status_code == 400
    assert not store.require(dirty_uploaded["deck_id"]).history


def test_resizing_needs_the_token_like_everything_else(store, tmp_path, monkeypatch):
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "profiles"))
    with TestClient(create_app(store)) as anonymous:
        response = anonymous.post(
            "/api/resize",
            json={"deck_id": "x", "client": "demo", "slide": 1, "shape_id": 1,
                  "cx_emu": 12700, "cy_emu": 12700},
        )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Editing text -- addressed positionally, like a run tieout_ui.canvas numbered
# --------------------------------------------------------------------------- #


def _text_shape(model, slide_index, name):
    slide = model.slide(slide_index)
    assert slide is not None
    return next(s for s in slide.all_shapes() if s.ref.name == name)


def test_editing_text_writes_the_run_and_can_be_undone(
    client, dirty_uploaded, onboarded, store
):
    deck_id = dirty_uploaded["deck_id"]
    _check(client, deck_id)
    model = store.require(deck_id).model
    shape = next(
        s
        for _, s in model.all_shapes()
        if s.has_text_frame and s.text_frame_paragraphs and s.text_frame_paragraphs[0].runs
    )
    original = shape.text_frame_paragraphs[0].runs[0].text

    edited = client.post(
        "/api/edit-text",
        json={
            "deck_id": deck_id, "client": "demo", "slide": shape.ref.slide_index,
            "shape_id": shape.ref.shape_id, "paragraph": 0, "run": 0,
            "text": "Edited from the canvas.",
        },
    )
    assert edited.status_code == 200, edited.text
    after = edited.json()
    assert after["edited"] is True and after["can_undo"] is True
    assert after["edited_text"].startswith("Edit text on")

    after_shape = _text_shape(store.require(deck_id).model, shape.ref.slide_index, shape.ref.name)
    assert after_shape.text_frame_paragraphs[0].runs[0].text == "Edited from the canvas."

    undone = client.post("/api/undo", json={"deck_id": deck_id, "client": "demo"})
    assert undone.status_code == 200, undone.text
    assert undone.json()["can_undo"] is False
    restored = _text_shape(store.require(deck_id).model, shape.ref.slide_index, shape.ref.name)
    assert restored.text_frame_paragraphs[0].runs[0].text == original


def test_an_edited_deck_exports_with_the_text_changed(
    client, dirty_uploaded, onboarded, store, tmp_path
):
    deck_id = dirty_uploaded["deck_id"]
    _check(client, deck_id)
    model = store.require(deck_id).model
    shape = next(
        s
        for _, s in model.all_shapes()
        if s.has_text_frame and s.text_frame_paragraphs and s.text_frame_paragraphs[0].runs
    )

    assert client.post(
        "/api/edit-text",
        json={
            "deck_id": deck_id, "client": "demo", "slide": shape.ref.slide_index,
            "shape_id": shape.ref.shape_id, "paragraph": 0, "run": 0,
            "text": "Exported with the edit.",
        },
    ).status_code == 200

    exported = client.get(f"/api/export/{deck_id}")
    assert exported.status_code == 200
    out = tmp_path / "exported.pptx"
    out.write_bytes(exported.content)
    after_shape = _text_shape(load_deck(out), shape.ref.slide_index, shape.ref.name)
    assert after_shape.text_frame_paragraphs[0].runs[0].text == "Exported with the edit."


def test_editing_text_on_a_shape_inside_a_group_is_not_refused(
    client, onboarded, store, tmp_path
):
    """Unlike a move or a resize: no coordinate space stands between an edit
    to a run's words and the group it happens to sit in."""
    path = _grouped_deck(tmp_path / "grouped-text.pptx")
    uploaded = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()
    deck = store.require(uploaded["deck_id"])
    inside = next(
        shape for _, shape in deck.model.all_shapes() if shape.ref.name == "Inside"
    )
    assert inside.ref.group_path

    response = client.post(
        "/api/edit-text",
        json={
            "deck_id": uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": inside.ref.shape_id, "paragraph": 0, "run": 0,
            "text": "Edited inside a group.",
        },
    )
    assert response.status_code == 200, response.text


def test_editing_a_shape_with_no_text_frame_is_refused(
    client, dirty_uploaded, onboarded, store
):
    model = store.require(dirty_uploaded["deck_id"]).model
    shape = next(s for _, s in model.all_shapes() if not s.has_text_frame)

    response = client.post(
        "/api/edit-text",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo",
            "slide": shape.ref.slide_index, "shape_id": shape.ref.shape_id,
            "paragraph": 0, "run": 0, "text": "anything",
        },
    )
    assert response.status_code == 422
    assert "no text frame" in response.json()["detail"]


def test_editing_a_paragraph_that_is_not_there_is_refused(
    client, dirty_uploaded, onboarded, store
):
    model = store.require(dirty_uploaded["deck_id"]).model
    shape = next(s for _, s in model.all_shapes() if s.has_text_frame)

    response = client.post(
        "/api/edit-text",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo",
            "slide": shape.ref.slide_index, "shape_id": shape.ref.shape_id,
            "paragraph": 999, "run": 0, "text": "anything",
        },
    )
    assert response.status_code == 422
    assert "paragraph 999" in response.json()["detail"]


def test_editing_text_to_what_it_already_is_writes_no_version(
    client, dirty_uploaded, onboarded, store
):
    model = store.require(dirty_uploaded["deck_id"]).model
    shape = next(
        s
        for _, s in model.all_shapes()
        if s.has_text_frame and s.text_frame_paragraphs and s.text_frame_paragraphs[0].runs
    )
    current = shape.text_frame_paragraphs[0].runs[0].text

    response = client.post(
        "/api/edit-text",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo",
            "slide": shape.ref.slide_index, "shape_id": shape.ref.shape_id,
            "paragraph": 0, "run": 0, "text": current,
        },
    )
    assert response.status_code == 400
    assert not store.require(dirty_uploaded["deck_id"]).history


def test_editing_a_shape_that_is_not_there_is_refused(client, dirty_uploaded, onboarded):
    _check(client, dirty_uploaded["deck_id"])
    response = client.post(
        "/api/edit-text",
        json={
            "deck_id": dirty_uploaded["deck_id"], "client": "demo", "slide": 1,
            "shape_id": 999_999, "paragraph": 0, "run": 0, "text": "anything",
        },
    )
    assert response.status_code == 404


def test_editing_text_needs_the_token_like_everything_else(store, tmp_path, monkeypatch):
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "profiles"))
    with TestClient(create_app(store)) as anonymous:
        response = anonymous.post(
            "/api/edit-text",
            json={"deck_id": "x", "client": "demo", "slide": 1, "shape_id": 1,
                  "paragraph": 0, "run": 0, "text": "anything"},
        )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# What a correction changed
#
# A new total is a number to compare from memory. These tests are about the
# three counts being distinct, reconciling, and — the part that matters — a
# correction that exposed a finding saying so loudly enough to act on.
# --------------------------------------------------------------------------- #


def test_the_first_check_reports_no_change(client, dirty_uploaded, onboarded):
    """Nothing has been done to the deck yet, so nothing is fixed and nothing is
    new: every finding is merely present."""
    view = _check(client, dirty_uploaded["deck_id"])
    change = view["delta"]
    assert (change["fixed"], change["new"]) == (0, 0)
    assert change["remaining"] == view["summary"]["total"]
    assert view["corrections"] == []


def test_every_finding_carries_an_identity_that_survives_a_re_audit(
    client, dirty_uploaded, onboarded
):
    first = _check(client, dirty_uploaded["deck_id"])
    again = _check(client, dirty_uploaded["deck_id"])
    ids = lambda view: sorted(f["id"] for s in view["slides"] for f in s["findings"])  # noqa: E731
    assert ids(first) == ids(again)
    assert all(ids(first)), "an empty identity would collapse unrelated findings"
    assert again["delta"]["fixed"] == 0 and again["delta"]["new"] == 0


def test_two_concurrent_fixes_do_not_corrupt_the_deck(
    client, dirty_uploaded, onboarded, store
):
    """Two clicks landing at the same moment must not race: FastAPI runs each
    sync route in its own thread, and without ``Deck.lock`` serialising them, a
    fix built from one request's read of ``deck.current`` can write over the
    version file another request is still building from -- or the two writes
    can interleave into a package that never opens at all."""
    import threading

    from pptx import Presentation

    deck_id = dirty_uploaded["deck_id"]
    view = _check(client, deck_id)
    fixable = [a for a in view["actions"] if a["fixable"]]
    assert len(fixable) >= 2, "the seeded deck needs at least two independent fixes"

    results: list[object] = [None, None]

    def apply(index: int, key: str) -> None:
        results[index] = client.post(
            "/api/fix", json={"deck_id": deck_id, "client": "demo", "key": key},
        )

    threads = [
        threading.Thread(target=apply, args=(0, fixable[0]["key"])),
        threading.Thread(target=apply, args=(1, fixable[1]["key"])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(r.status_code == 200 for r in results), [r.text for r in results]  # type: ignore[attr-defined]
    deck = store.require(deck_id)
    assert len(deck.history) == 2
    assert len(deck.log) == 2
    Presentation(str(deck.current))  # opens cleanly; neither write clobbered the other

    after = _check(client, deck_id)
    remaining = {a["key"] for a in after["actions"] if a["fixable"]}
    assert fixable[0]["key"] not in remaining
    assert fixable[1]["key"] not in remaining


def test_a_correction_says_what_it_fixed_and_what_is_left(
    client, dirty_uploaded, onboarded
):
    view = _check(client, dirty_uploaded["deck_id"])
    fixable = [a for a in view["actions"] if a["fixable"]]
    assert fixable, "the seeded deck has mechanical corrections"
    before = view["summary"]["total"]

    after = client.post(
        "/api/fix",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo",
              "key": fixable[0]["key"]},
    ).json()
    change = after["delta"]

    assert change["fixed"] > 0
    assert change["fixed"] + change["remaining"] == before == change["before"]
    assert change["remaining"] + change["new"] == change["after"]
    assert change["sentence"].startswith(f"{change['fixed']} fixed")


def test_the_correction_log_carries_what_each_one_changed(
    client, dirty_uploaded, onboarded
):
    """The summary that goes out with the export. "Three corrections made" does
    not say whether they helped."""
    view = _check(client, dirty_uploaded["deck_id"])
    fixable = [a for a in view["actions"] if a["fixable"]][:2]
    assert len(fixable) == 2

    for action in fixable:
        latest = client.post(
            "/api/fix",
            json={"deck_id": dirty_uploaded["deck_id"], "client": "demo",
                  "key": action["key"]},
        ).json()

    log = latest["corrections"]
    assert len(log) == 2
    assert [entry["summary"] for entry in log] == latest["applied"]
    for entry in log:
        assert entry["delta"] is not None, "every applied correction knows its delta"
        assert entry["delta"]["fixed"] + entry["delta"]["remaining"] == entry["delta"]["before"]


def test_an_undo_reports_the_findings_coming_back(client, dirty_uploaded, onboarded):
    view = _check(client, dirty_uploaded["deck_id"])
    fixable = [a for a in view["actions"] if a["fixable"]]
    assert fixable
    fixed = client.post(
        "/api/fix",
        json={"deck_id": dirty_uploaded["deck_id"], "client": "demo",
              "key": fixable[0]["key"]},
    ).json()

    undone = client.post(
        "/api/undo", json={"deck_id": dirty_uploaded["deck_id"], "client": "demo"}
    ).json()
    assert undone["delta"]["new"] == fixed["delta"]["fixed"]
    assert undone["delta"]["after"] == view["summary"]["total"]
    assert undone["corrections"] == []


def _overlapping_deck(path):
    """Two columns that do not overlap, so that moving one over the other makes
    a finding that was not there before."""
    from pptx import Presentation
    from pptx.util import Emu, Pt

    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    for index, left in enumerate((60, 500)):
        box = slide.shapes.add_textbox(Pt(left), Pt(200), Pt(300), Pt(80))
        box.name = f"Column {index}"
        box.text_frame.text = f"Column {index} carries a sentence of body copy."
    presentation.save(str(path))
    return path


def test_a_move_that_exposes_a_finding_marks_it_new_where_it_is_read(
    client, onboarded, store, tmp_path
):
    """The hard case, end to end.

    Moving a shape onto its neighbour reports an overlap that was not there
    before. Counting it is not enough — the finding itself is flagged on the
    slide it arrived on, so the person can see what the correction cost and
    decide whether to undo it.
    """
    path = _overlapping_deck(tmp_path / "columns.pptx")
    deck_id = client.post(
        "/api/decks", files={"file": (path.name, path.read_bytes(), _MIME)}
    ).json()["deck_id"]

    before = _check(client, deck_id)
    assert not [
        f for s in before["slides"] for f in s["findings"] if f["rule_id"] == "LO-004"
    ], "the columns do not overlap yet"

    mover = next(
        shape
        for _, shape in store.require(deck_id).model.all_shapes()
        if shape.ref.name == "Column 1"
    )
    moved = client.post(
        "/api/move",
        json={"deck_id": deck_id, "client": "demo", "slide": 1,
              "shape_id": mover.ref.shape_id,
              "x_emu": 70 * 12700, "y_emu": 200 * 12700},
    )
    assert moved.status_code == 200, moved.text
    view = moved.json()

    change = view["delta"]
    assert change["new"] >= 1
    assert "LO-004" in {entry["rule_id"] for entry in change["arrived"]}
    assert change["worst_new"] is not None

    # Named where it is read, not only counted at the top.
    flagged = [f for s in view["slides"] for f in s["findings"] if f.get("arrived")]
    assert {f["rule_id"] for f in flagged} >= {"LO-004"}
    assert {f["id"] for f in flagged} == {e["id"] for e in change["arrived"]}
    assert any(a.get("arrived") for a in view["actions"])

    # And it can be taken back.
    assert view["can_undo"] is True
    undone = client.post(
        "/api/undo", json={"deck_id": deck_id, "client": "demo"}
    ).json()
    assert not [
        f for s in undone["slides"] for f in s["findings"] if f["rule_id"] == "LO-004"
    ]
