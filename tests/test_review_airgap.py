"""The separation between the offline tool and the optional online layer.

``tieout`` promises it cannot reach a network, and ``tests/test_no_network.py``
proves that by walking the syntax tree of every module in the package. This file
is the other half: that the review layer cannot quietly become part of that
package, and that within the review layer the network is confined to one module
small enough to read.

Structural rather than behavioural on purpose. A test that no call went out on
one code path proves nothing about the next one.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import tieout
import tieout_review
import tieout_ui
from tests.test_no_network import FORBIDDEN_MODULES

_CORE = Path(tieout.__file__).parent
_REVIEW = Path(tieout_review.__file__).parent
_UI = Path(tieout_ui.__file__).parent

#: The only module allowed to reach out, and the only one the tests below
#: exempt. Keeping the transport this contained is what makes the claim
#: checkable rather than aspirational.
_TRANSPORT = "client.py"


def _modules(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module.split(".")[0]))
    return out


def test_the_optional_packages_are_outside_the_air_gapped_one():
    """Two top-level packages, not a subpackage.

    A subpackage would sit inside the walk's root and would have to be excluded
    from it by name, which is exactly the kind of exception that stops being
    noticed.
    """
    for package in (_REVIEW, _UI):
        assert package != _CORE
        assert _CORE not in package.parents


def test_the_air_gap_walk_treats_both_optional_packages_as_forbidden():
    """So that a core module importing one is a test failure, not a review note."""
    assert "tieout_review" in FORBIDDEN_MODULES
    assert "tieout_ui" in FORBIDDEN_MODULES
    assert "fastapi" in FORBIDDEN_MODULES


@pytest.mark.parametrize("path", _modules(_CORE), ids=lambda p: p.name)
def test_no_core_module_imports_the_ui_layer(path):
    offenders = [
        f"line {line}: {module}"
        for line, module in _imports(path)
        if module in ("tieout_ui", "fastapi", "starlette", "uvicorn")
    ]
    assert not offenders, f"{path.relative_to(_CORE)} imports the UI layer: {offenders}"


@pytest.mark.parametrize("path", _modules(_UI), ids=lambda p: p.name)
def test_the_ui_layer_reaches_the_network_only_through_the_review_transport(path):
    """The UI serves a loopback socket, which is what a web framework is for.

    What it must not do is talk to a third party itself: the only outbound call
    in the whole repository stays in ``tieout_review.client``, reached through
    one named seam.
    """
    allowed = {"fastapi", "starlette", "uvicorn", "tieout_review", "socket"}
    if path.name == "cli.py":
        # It opens a browser on the loopback URL it just bound. Covered by the
        # test below, which pins the address it is allowed to open.
        allowed.add("webbrowser")
    offenders = [
        f"line {line}: {module}"
        for line, module in _imports(path)
        if module in FORBIDDEN_MODULES - allowed - {"tieout_ui"}
    ]
    assert not offenders, f"{path.relative_to(_UI)} can reach out: {offenders}"


def test_the_browser_is_only_ever_opened_on_a_loopback_address():
    """The one ``webbrowser`` call in the repository. It must not be reachable
    with anything but the address the server just bound, which the CLI has
    already refused unless it is loopback."""
    from tieout_ui.cli import _launch

    opened: list[str] = []

    class _FakeTimer:
        def __init__(self, delay: float, function: object) -> None:
            self._function = function

        def start(self) -> None:
            self._function()  # type: ignore[operator]

    import threading
    import webbrowser

    from tieout_ui.server import is_loopback

    def _record(url: str, *args: object, **kwargs: object) -> bool:
        opened.append(url)
        return True

    real_timer, real_open = threading.Timer, webbrowser.open
    threading.Timer = _FakeTimer  # type: ignore[misc, assignment]
    webbrowser.open = _record
    try:
        _launch("http://127.0.0.1:8765/", "token-value")
    finally:
        threading.Timer = real_timer  # type: ignore[misc]
        webbrowser.open = real_open

    (url,) = opened
    host = url.split("//", 1)[1].split(":", 1)[0]
    assert is_loopback(host), url


def test_the_ui_never_imports_the_sdk_itself():
    """It goes through the review layer's transport or not at all."""
    for path in _modules(_UI):
        assert "anthropic" not in {module for _, module in _imports(path)}, path


def test_the_served_page_has_no_external_references():
    """The same promise the HTML report makes, for the same reason: the page has
    a live deck open in it."""
    from tieout_ui.server import PAGE

    markup = PAGE.read_text(encoding="utf-8")
    for forbidden in (
        "http://",
        "https://",
        "//cdn",
        "fonts.googleapis",
        "fonts.gstatic",
        "unpkg",
        "jsdelivr",
        "<script src",
        "@import url",
    ):
        assert forbidden not in markup, f"the page references {forbidden!r}"


@pytest.mark.parametrize("path", _modules(_CORE), ids=lambda p: p.name)
def test_no_core_module_imports_the_review_layer(path):
    offenders = [
        f"line {line}: {module}"
        for line, module in _imports(path)
        if module == "tieout_review"
    ]
    assert not offenders, f"{path.relative_to(_CORE)} imports the review layer: {offenders}"


@pytest.mark.parametrize("path", _modules(_REVIEW), ids=lambda p: p.name)
def test_only_the_transport_module_can_reach_the_network(path):
    """Everything else here — redaction, extraction, term assembly, parsing —
    runs offline and is testable without a key."""
    allowed = {"anthropic"} if path.name == _TRANSPORT else set()
    offenders = [
        f"line {line}: {module}"
        for line, module in _imports(path)
        if module in FORBIDDEN_MODULES - allowed - {"tieout_review"}
    ]
    assert not offenders, f"{path.relative_to(_REVIEW)} can reach the network: {offenders}"


def test_the_redaction_boundary_imports_nothing_but_the_standard_library_and_tieout():
    """The module that decides what leaves the machine should be readable in one
    sitting, and that starts with its dependencies."""
    roots = {module for _, module in _imports(_REVIEW / "redact.py")}
    assert roots <= {
        "__future__",
        "collections",
        "dataclasses",
        "re",
        "typing",
        "tieout",
        "tieout_review",
    }, roots


def test_importing_the_review_package_does_not_load_the_sdk():
    """``tieout-review redact`` has to work on a machine with no SDK installed —
    which is the machine someone evaluating the redaction would rather be on."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import tieout_review; "
            "print('anthropic' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stdout


def test_importing_the_review_cli_does_not_load_the_sdk():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import tieout_review.cli; print('anthropic' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stdout


def test_the_api_key_is_never_in_a_repr():
    """An exception in the local UI must not be able to print a key."""
    from tieout_review.client import AnthropicReviewClient

    client = AnthropicReviewClient(api_key="sk-ant-do-not-print-me")
    assert "do-not-print-me" not in repr(client)
    assert "do-not-print-me" not in str(client)


def test_the_transport_refuses_to_exist_without_a_key(monkeypatch):
    """Failing at construction rather than mid-request keeps the error readable."""
    from tieout_review.client import API_KEY_ENV, AnthropicReviewClient, ReviewClientError

    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ReviewClientError):
        AnthropicReviewClient()


def test_the_key_is_not_written_to_a_profile_or_a_report():
    """Searched for structurally: no module in any package writes a key out."""
    for root in (_CORE, _REVIEW, _UI):
        for path in _modules(root):
            source = path.read_text(encoding="utf-8")
            assert "ANTHROPIC_API_KEY" not in source or path.name == _TRANSPORT


# --------------------------------------------------------------------------- #
# The single call site
# --------------------------------------------------------------------------- #
#
# Everything above is about which modules *can* reach the network. This is about
# where, in the one module that can, the outbound call actually happens — and
# the argument for asserting it structurally is the argument this whole file
# makes: a test proving that one code path refused to send proves nothing about
# a second path added next month. There is one call, it is in ``send``, and the
# hold, the re-verification and the outbound record all execute before it. That
# is the safety property, and it is a property of the shape of the code rather
# than of any run of it.


def _complete_calls(root: Path) -> list[tuple[Path, int]]:
    """Every ``<something>.complete(...)`` in a package, with its line."""
    found: list[tuple[Path, int]] = []
    for path in _modules(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "complete"
        )
    return found


def test_the_transport_is_called_from_exactly_one_place():
    """Two call sites would mean two places to get the hold right."""
    calls = [
        (path, line)
        for root in (_CORE, _REVIEW, _UI)
        for path, line in _complete_calls(root)
    ]
    assert len(calls) == 1, f"expected one call to client.complete(), found: {calls}"
    (path, _) = calls[0]
    assert path.name == "review.py", path


def _send_body() -> list[ast.stmt]:
    tree = ast.parse((_REVIEW / "review.py").read_text(encoding="utf-8"))
    send = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "send"
    )
    return send.body


def test_the_one_call_site_is_inside_send():
    body = _send_body()
    lines = {
        node.lineno
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "complete"
    }
    assert len(lines) == 1, f"send() should hold the only complete() call: {lines}"


def test_nothing_is_transmitted_before_the_hold_the_verification_and_the_record():
    """The order of the four statements, read off the syntax tree.

    Reordering any of them still passes every behavioural test in the suite —
    the stub client does not care when it is called — and would quietly undo the
    guarantee. Hence this.
    """
    body = _send_body()

    def index_of(predicate: object) -> int:
        for position, statement in enumerate(body):
            for node in ast.walk(statement):
                if predicate(node):  # type: ignore[operator]
                    return position
        raise AssertionError(f"send() no longer contains {predicate}")

    held = index_of(
        lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "RedactionHeld"
    )
    verified = index_of(
        lambda n: isinstance(n, ast.Attribute) and n.attr == "verify"
    )
    recorded = index_of(
        lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "record_outbound"
    )
    transmitted = index_of(
        lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "complete"
    )

    assert held < transmitted, "the hold must be checked before anything is sent"
    assert verified < transmitted, "the re-verification must run before anything is sent"
    assert recorded < transmitted, (
        "the outbound record must be written before the payload is handed over, "
        "or a send that dies mid-flight leaves no evidence that it happened"
    )
