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

import pytest

from tieout.fixtures.generator import build_all
from tieout.learn import learn_from_decks
from tieout.learn.emit import write as write_profile
from tieout.model.loader import load_deck
from tieout.rules.base import clear_caches

JOURNEY = Path(__file__).parent / "browser" / "journey.js"


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
def journey(tmp_path_factory: pytest.TempPathFactory):
    node = _node_with_playwright()
    if node is None:
        pytest.skip("needs node with the playwright package")
    directory = Path(tmp_path_factory.mktemp("browser"))
    built = build_all(directory / "decks")
    clear_caches()
    profiles = directory / "profiles"
    write_profile(
        learn_from_decks([load_deck(str(built.clean))], "falcon").profile,
        profiles / "falcon.yaml",
    )
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
        run = subprocess.run(
            [node, str(JOURNEY), url, str(built.dirty), "falcon"],
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, "NODE_PATH": _node_path()},
            check=False,
        )
        lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
        if not lines:
            pytest.fail(f"the journey printed nothing:\n{run.stdout}\n{run.stderr}")
        result = json.loads(lines[-1])
        if "crashed" in result and "Executable doesn't exist" in result["crashed"]:
            pytest.skip("playwright has no browser it can launch here")
        return result
    finally:
        server.terminate()
        server.wait(timeout=10)


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


def test_a_reload_reopens_the_deck_with_its_corrections(journey) -> None:
    """The audit's #18."""
    assert journey["fixed"]
    assert journey["ribbonAfter"] == journey["ribbonBefore"]
    assert "Reopened" in journey["flashAfterReload"]
