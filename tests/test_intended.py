""""This is intentional": dismissed once, for the house style, by what it is about.

PLAN.md §0, item 4. A false positive that can be dismissed permanently in one
click is a nuisance; one that returns every run is why tools get switched off.
``--accept`` already existed and remembered a finding by its *slide number*, in
a file beside the profile -- so it held for exactly one deck, and the next turn
of the deck, with one slide inserted, brought every dismissed finding back.

What is stored here is a finding's signature: what it is about, never where.
The tests are built so that the place changes and the thing does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.corpus import (
    _osprey_content,
    _osprey_divider,
    build_osprey_reference,
    build_osprey_target,
)
from tests.test_real_world_figures import _deck
from tieout.cli import app
from tieout.learn import learn_from_decks
from tieout.learn.emit import write as write_profile
from tieout.learn.merge import merge
from tieout.model.loader import load_deck
from tieout.profile.loader import PROFILE_DIR_ENV, load, profile_path
from tieout.rules.base import clear_caches, run_rules
from tieout_ui.server import create_app
from tieout_ui.session import SessionStore

runner = CliRunner()
_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    directory = tmp_path / "profiles"
    monkeypatch.setenv(PROFILE_DIR_ENV, str(directory))
    return directory


@pytest.fixture
def osprey(tmp_path, profiles):
    """The Osprey house style learned through the CLI, and a deck checked in it."""
    reference = build_osprey_reference(tmp_path / "reference.pptx")
    target = build_osprey_target(tmp_path / "target.pptx")
    result = runner.invoke(app, ["learn", str(reference), "--client", "osprey"])
    assert result.exit_code == 0, result.output
    return target


def _later_deck(path: Path) -> Path:
    """The next deck in the house style: the nudged box from the target's slide
    3 is now on slide 6, behind a divider and four slides that were not there.
    Anything keyed on the slide number misses it."""
    presentation = _deck()
    _osprey_divider(presentation, 1, "Update", bleed=False)
    for page, title in enumerate(("Market", "Company", "Strategy", "Customers"), start=2):
        _osprey_content(presentation, page, title)
    _osprey_content(presentation, 6, "Market", right=493)
    presentation.save(str(path))
    return path


def _findings(deck_path: Path, client: str, rule_id: str) -> list[str]:
    clear_caches()
    profile = load(profile_path(client))
    result = run_rules(load_deck(str(deck_path)), profile, include=[rule_id])
    return [f"slide {f.slide_index}: {f.message}" for f in result.findings]


# --------------------------------------------------------------------------------------
# The promise
# --------------------------------------------------------------------------------------


def test_a_declaration_holds_on_another_deck_at_another_slide(osprey, tmp_path) -> None:
    assert _findings(osprey, "osprey", "LO-003"), "the corpus's nudge must be reported first"

    result = runner.invoke(
        app,
        [
            "check", str(osprey), "--client", "osprey", "--rules", "LO-003",
            "--intended", "LO-003@slide3:Body nudged",
            "--intended-note", "the right column is inset on market slides",
        ],
    )
    assert "Declared 1 finding(s) intentional" in result.output, result.output
    assert not _findings(osprey, "osprey", "LO-003")

    later = _later_deck(tmp_path / "later.pptx")
    assert not _findings(later, "osprey", "LO-003")

    (entry,) = load(profile_path("osprey")).intended
    assert "slide" not in entry.signature
    assert entry.note == "the right column is inset on market slides"


def test_a_declaration_does_not_cover_a_different_oddity(osprey, tmp_path) -> None:
    """Declaring 493pt intended says nothing about 487pt."""
    runner.invoke(
        app,
        ["check", str(osprey), "--client", "osprey", "--intended", "LO-003@slide3"],
    )
    presentation = _deck()
    _osprey_content(presentation, 1, "Market")
    _osprey_content(presentation, 2, "Company", right=486.5)
    path = tmp_path / "other.pptx"
    presentation.save(str(path))
    assert _findings(path, "osprey", "LO-003")


def test_a_declared_finding_is_excused_out_loud(osprey) -> None:
    runner.invoke(
        app,
        ["check", str(osprey), "--client", "osprey", "--intended", "LO-003@slide3",
         "--intended-note", "inset on purpose"],
    )
    clear_caches()
    result = run_rules(
        load_deck(str(osprey)), load(profile_path("osprey")), include=["LO-003"]
    )
    reasons = [e.reason for e in result.excused if e.slide_index == 3]
    assert reasons == ["declared intentional for this house style: inset on purpose"]


def test_an_entry_naming_nothing_is_refused(osprey) -> None:
    result = runner.invoke(
        app, ["check", str(osprey), "--client", "osprey", "--intended", "LO-003@slide2"]
    )
    assert result.exit_code != 0
    assert "names no finding" in result.output
    assert not load(profile_path("osprey")).intended


# --------------------------------------------------------------------------------------
# Surviving the profile's own life
# --------------------------------------------------------------------------------------


def test_a_relearn_keeps_what_a_person_declared(osprey, tmp_path) -> None:
    """Nothing in a reference deck can re-derive "this was meant", and a fresh
    learn used to write straight over the file."""
    runner.invoke(
        app, ["check", str(osprey), "--client", "osprey", "--intended", "LO-003@slide3"]
    )
    reference = tmp_path / "reference.pptx"
    result = runner.invoke(app, ["learn", str(reference), "--client", "osprey"])
    assert "Kept 1 finding(s) previously declared intentional" in result.output
    assert len(load(profile_path("osprey")).intended) == 1
    assert not _findings(osprey, "osprey", "LO-003")


def test_a_merge_keeps_both_sides_declarations(tmp_path) -> None:
    clear_caches()
    deck = load_deck(str(build_osprey_reference(tmp_path / "reference.pptx")))
    one = learn_from_decks([deck], "osprey").profile
    other = one.model_copy(deep=True)
    one.declare_intended("LO-003", "left 493pt against 490pt")
    other.declare_intended("LO-001", "autoshape|untexted|760,-100,374x374")
    merged = merge(one, other).profile
    assert {e.rule_id for e in merged.intended} == {"LO-003", "LO-001"}


def test_a_cluster_is_excused_only_when_every_instance_is(tmp_path) -> None:
    """Declaring one shape off the grid intended says nothing about the four
    beside it, so a clustered finding stays reported until all are declared."""
    clear_caches()
    deck = load_deck(str(build_osprey_reference(tmp_path / "reference.pptx")))
    profile = learn_from_decks([deck], "osprey").profile
    profile.declare_intended("LO-003", "left 493pt against 490pt")
    assert profile.intends("LO-003", "left 493pt against 490pt")
    assert profile.intends("LO-003", "left 493pt against 490pt\nleft 487pt against 490pt") is None
    profile.declare_intended("LO-003", "left 487pt against 490pt")
    assert profile.intends("LO-003", "left 493pt against 490pt\nleft 487pt against 490pt")
    assert profile.withdraw_intended("LO-003", "left 487pt against 490pt") == 1
    assert profile.intends("LO-003", "left 487pt against 490pt") is None


def test_a_figure_pair_is_declared_by_what_it_is_about(tmp_path) -> None:
    """CO-001's signature is the fact and the two values, never the slides --
    so a pair declared intended stays quiet until either figure changes."""
    from tests.test_figure_kinds import _audit

    _, result = _audit(
        tmp_path, ["Revenue grew 17% in FY25A.", "15% revenue growth in FY25A."]
    )
    (finding,) = result.findings
    assert finding.signature == "revenue||change|FY2025A|15%~17%"


# --------------------------------------------------------------------------------------
# The button
# --------------------------------------------------------------------------------------


@pytest.fixture
def server(profiles, tmp_path):
    clear_caches()
    reference = load_deck(str(build_osprey_reference(tmp_path / "reference.pptx")))
    write_profile(learn_from_decks([reference], "osprey").profile, profile_path("osprey"))
    store = SessionStore()
    with TestClient(create_app(store)) as client:
        client.headers.update({"X-TieOut-Token": store.token})
        target = build_osprey_target(tmp_path / "target.pptx")
        uploaded = client.post(
            "/api/decks", files={"file": (target.name, target.read_bytes(), _MIME)}
        ).json()
        yield client, uploaded["deck_id"]
    store.close()


def _nudge(view):
    (action,) = [a for a in view["actions"] if a["rule_id"] == "LO-003"]
    return action


def test_the_button_writes_the_profile_and_undo_takes_it_back(server) -> None:
    client, deck_id = server
    checked = client.post("/api/check", json={"deck_id": deck_id, "client": "osprey"}).json()
    action = _nudge(checked)

    marked = client.post(
        "/api/intended", json={"deck_id": deck_id, "client": "osprey", "key": action["key"]}
    )
    assert marked.status_code == 200, marked.text
    view = marked.json()
    assert not [a for a in view["actions"] if a["rule_id"] == "LO-003"]
    assert any("declared intentional" in e["reason"] for e in view["excused"])
    assert len(load(profile_path("osprey")).intended) == 1

    said = view["intended"]
    undone = client.post(
        "/api/intended",
        json={"deck_id": deck_id, "client": "osprey", "withdraw": True,
              "rule_id": said["rule_id"], "signatures": said["signatures"]},
    ).json()
    assert _nudge(undone)
    assert not load(profile_path("osprey")).intended


def test_the_house_style_lists_and_withdraws_without_a_deck(server) -> None:
    client, deck_id = server
    checked = client.post("/api/check", json={"deck_id": deck_id, "client": "osprey"}).json()
    client.post(
        "/api/intended",
        json={"deck_id": deck_id, "client": "osprey", "key": _nudge(checked)["key"]},
    )
    style = client.get("/api/profiles/osprey").json()
    (entry,) = style["intended"]
    withdrawn = client.post(
        "/api/intended",
        json={"client": "osprey", "withdraw": True, "rule_id": entry["rule_id"],
              "signatures": [entry["signature"]]},
    ).json()
    assert withdrawn["profile"]["intended"] == []


def test_a_stale_action_is_refused_rather_than_ignored(server) -> None:
    client, deck_id = server
    response = client.post(
        "/api/intended", json={"deck_id": deck_id, "client": "osprey", "key": "LO-003|gone"}
    )
    assert response.status_code == 409


def test_the_page_offers_the_button(server) -> None:
    client, _ = server
    page = client.get("/").text
    assert "This is intentional" in page and "data-intend" in page
    assert "/api/intended" in page
