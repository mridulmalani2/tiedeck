"""The page, driven in a real browser.

Every UI change in PLAN.md §11 landed with a source-level test -- a string that
must or must not be in the served page -- because nothing in this suite drove a
browser. Re-driving the app for §0 found what that costs: the fix for the
audit's #30 (no profile pre-selected) made **Use existing profile** refuse
every deck with "No client has been onboarded yet", because the guard read the
selection the fix had just emptied. The main path through the product was
unreachable, and every source-level test passed.

This drives one journey -- open, choose a house style, check, declare a finding
intentional and undo it, correct, reload -- in Chromium, against the real
server. It needs Node, the ``playwright`` package and a Chromium it can launch,
and skips without them rather than failing a machine that has none.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tieout.fixtures.generator import build_all
from tieout.learn import learn_from_decks
from tieout.learn.emit import write as write_profile
from tieout.model.loader import load_deck
from tieout.rules.base import clear_caches

JOURNEY = Path(__file__).parent / "browser" / "journey.js"
GROUPS = Path(__file__).parent / "browser" / "groups.js"


def _node_with_playwright() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    probe = subprocess.run(
        [node, "-e", "require.resolve('playwright')"],
        capture_output=True,
        env={**os.environ, "NODE_PATH": _node_path()},
        check=False,
    )
    return node if probe.returncode == 0 else None


def _node_path() -> str:
    found = shutil.which("node")
    guesses = [os.environ.get("NODE_PATH", "")]
    if found:
        guesses.append(str(Path(found).resolve().parent.parent / "lib" / "node_modules"))
    return os.pathsep.join(g for g in guesses if g)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory):
    """The decks, two house styles, and a server: falcon is the reference
    deck's own, osprey a foreign one, so the clean deck checked against it
    produces hundreds of findings in a few jobs."""
    node = _node_with_playwright()
    if node is None:
        pytest.skip("needs node with the playwright package")
    from tests.corpus import build_osprey_reference

    directory = Path(tmp_path_factory.mktemp("browser"))
    built = build_all(directory / "decks")
    clear_caches()
    profiles = directory / "profiles"
    write_profile(
        learn_from_decks([load_deck(str(built.clean))], "falcon").profile,
        profiles / "falcon.yaml",
    )
    clear_caches()
    osprey = load_deck(str(build_osprey_reference(directory / "osprey.pptx")))
    write_profile(learn_from_decks([osprey], "osprey").profile, profiles / "osprey.yaml")
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "tieout_ui.cli", "--no-open", "--port", str(port)],
        env={**os.environ, "TIEOUT_PROFILE_DIR": str(profiles)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/"
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        yield node, url, built
    finally:
        server.terminate()
        server.wait(timeout=10)


def _drive(node: str, script: Path, *args: str) -> dict[str, Any]:
    run = subprocess.run(
        [node, str(script), *args],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "NODE_PATH": _node_path()},
        check=False,
    )
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    if not lines:
        pytest.fail(f"{script.name} printed nothing:\n{run.stdout}\n{run.stderr}")
    result: dict[str, Any] = json.loads(lines[-1])
    if "crashed" in result and "Executable doesn't exist" in result["crashed"]:
        pytest.skip("playwright has no browser it can launch here")
    return result


@pytest.fixture(scope="module")
def journey(site):
    node, url, built = site
    return _drive(node, JOURNEY, url, str(built.dirty), "falcon")


@pytest.fixture(scope="module")
def groups(site):
    node, url, built = site
    return _drive(node, GROUPS, url, str(built.clean), "osprey")


def test_the_page_raised_nothing(journey) -> None:
    assert "crashed" not in journey, journey
    assert journey["errors"] == []


def test_an_existing_house_style_can_be_chosen(journey) -> None:
    """The regression this file exists for."""
    assert journey["existingShown"], journey["flashOnPick"]


def test_this_is_intentional_takes_a_finding_off_and_undo_puts_it_back(journey) -> None:
    assert journey["intendButtons"] > 0
    assert "Marked intentional for falcon's house style" in journey["flashOnIntend"]
    assert journey["tallyIntended"] != journey["tallyChecked"]
    assert journey["tallyUndone"] == journey["tallyChecked"]


def test_a_page_number_can_be_edited_with_or_without_the_editor_open(journey) -> None:
    """The audit's #26. The digit sits a few pixels inside a padded box, so a
    double-click almost never landed on it -- and once the move editor was
    open, its box covered the digit entirely."""
    assert journey["pageNumberFound"]
    assert journey["pageNumberEditable"]
    assert journey["editorOpened"]
    assert journey["pageNumberEditableFromEditor"]


def test_a_chart_is_drawn_from_the_slide_render_where_there_is_one(journey) -> None:
    """The audit's #33: hatching beside a rendered picture of the same chart.
    Where this machine cannot render slides there is no picture to draw, and
    the hatching is the honest answer."""
    assert journey["chartFound"]
    assert journey["chartRaster"] == journey["rendered"]


def test_text_running_past_its_box_is_told_apart_from_the_canvas(journey) -> None:
    """The audit's #36: drawn past the box, deliberately, and marked."""
    assert journey["spills"]["seeded"] >= 1
    assert journey["spills"]["clean"] == 0


def test_a_reload_reopens_the_deck_with_its_corrections(journey) -> None:
    """The audit's #18."""
    assert journey["fixed"]
    assert journey["ribbonAfter"] == journey["ribbonBefore"]
    assert "Reopened" in journey["flashAfterReload"]


# -- the 26-slide deck against a foreign house style ---------------------------


def test_the_note_says_how_many_findings_its_jobs_collapse(groups) -> None:
    """The audit's #46: "16 things to do" beside "By slide (229)" and nothing
    relating the two."""
    assert groups["findings"] > groups["jobs"]
    assert f"from {groups['findings']} findings" in groups["totals"]


def test_a_job_no_control_can_do_is_given_its_real_reason(groups) -> None:
    """The audit's #47: "Add the page number at left 900pt..." was told TieOut
    "cannot know what is right" beside the exact answer."""
    for reason in groups["reasons"]:
        if reason["remedy"].startswith("Add "):
            assert "does not add a shape" in reason["why"], reason
        if reason["remedy"].startswith("Recolour "):
            assert "knows the colour" in reason["why"], reason


def test_move_it_walks_a_group_and_moves_the_right_shape(groups) -> None:
    """The audit's #45, and what driving it found behind it: the page-number
    findings named the wrong shape, so the walk moved the footnote."""
    assert groups["errors"] == []
    assert groups["groupFound"]
    assert "Place 1 of" in groups["first"]
    assert "Place 2 of" in groups["second"]
    assert groups["afterApply"].startswith(f"Move {groups['secondShape']} ")
    assert "still to do in this job" in groups["afterApply"]
    assert groups["editorAfterApply"]
