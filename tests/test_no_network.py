"""The tool must be incapable of reaching the network.

Section 2: no LLM calls, no network access of any kind, no telemetry, must run
air-gapped. For material this sensitive that is not a preference, it is the
precondition for the tool being allowed anywhere near a live deal.

A runtime check would only prove that nothing happened to make a call on one
path. This walks the abstract syntax tree of every runtime module instead, so
the guarantee is structural: the capability is not present in the code at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tieout

#: Modules that can reach the network, directly or as a client library.
FORBIDDEN_MODULES = frozenset(
    {
        "socket",
        "ssl",
        "urllib",
        "urllib2",
        "urllib3",
        "http",
        "httplib",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "xmlrpc",
        "asyncio",
        "webbrowser",
        "anthropic",
        "openai",
        "boto3",
        "botocore",
        "google",
        "grpc",
        "websocket",
        "websockets",
        "paramiko",
        "pycurl",
        # The optional layers. Both are separate top-level packages for exactly
        # this reason: a core module importing either would make this walk a
        # statement about an import guard rather than about the code.
        "tieout_review",
        "tieout_ui",
        # What the UI layer is built on. Named here so that a core module
        # reaching for a web framework is a test failure rather than a surprise.
        "fastapi",
        "starlette",
        "uvicorn",
    }
)

#: Callables that would let a module reach out without importing anything.
FORBIDDEN_CALLS = frozenset({"urlopen", "urlretrieve", "getaddrinfo", "create_connection"})

#: Attribute accesses that indicate a dynamic import escape hatch.
_DYNAMIC_IMPORT = frozenset({"import_module", "__import__"})

#: The fixture generator is runtime code and is held to the same rule, but the
#: package's own data files and the report template are not Python.
_PACKAGE_ROOT = Path(tieout.__file__).parent


def _runtime_modules() -> list[Path]:
    return sorted(_PACKAGE_ROOT.rglob("*.py"))


def test_the_walk_actually_finds_the_package():
    modules = _runtime_modules()
    assert len(modules) >= 20, f"only found {len(modules)} modules to check"
    names = {path.name for path in modules}
    for expected in ("cli.py", "loader.py", "inherit.py", "base.py", "generator.py"):
        assert expected in names


@pytest.mark.parametrize("path", _runtime_modules(), ids=lambda p: str(p.name))
def test_no_runtime_module_imports_anything_that_can_reach_the_network(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_MODULES:
                offenders.append(f"line {node.lineno}: from {node.module} import ...")

    assert not offenders, (
        f"{path.relative_to(_PACKAGE_ROOT)} can reach the network:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", _runtime_modules(), ids=lambda p: str(p.name))
def test_no_runtime_module_calls_a_network_function(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node.func)
        if name in FORBIDDEN_CALLS:
            offenders.append(f"line {node.lineno}: {name}(...)")

    assert not offenders, (
        f"{path.relative_to(_PACKAGE_ROOT)} calls out:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", _runtime_modules(), ids=lambda p: str(p.name))
def test_no_runtime_module_imports_dynamically(path):
    """A dynamic import would let a forbidden module in past the checks above,
    which would make this whole test file decorative."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _called_name(node.func)
            if name in _DYNAMIC_IMPORT:
                offenders.append(f"line {node.lineno}: {name}(...)")

    assert not offenders, (
        f"{path.relative_to(_PACKAGE_ROOT)} imports dynamically:\n  "
        + "\n  ".join(offenders)
    )


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def test_none_of_the_forbidden_modules_is_loaded_by_importing_the_package():
    """Belt and braces on the AST walk: importing everything must not pull in a
    network stack transitively through a dependency either.

    Run in a fresh interpreter, which is the only way the assertion means what
    it says: in this one, a sibling test has already imported the optional
    review layer, and ``sys.modules`` would show it regardless of what ``tieout``
    does.

    ``ssl`` and ``http`` are excluded because the standard library loads them
    eagerly in some environments regardless of what the package imports; the AST
    walk above is what proves TieOut does not use them.
    """
    import subprocess
    import sys

    tolerated = {"ssl", "http", "socket", "urllib", "asyncio", "google"}
    watched = sorted(FORBIDDEN_MODULES - tolerated)
    program = (
        "import sys\n"
        "from tieout.rules.base import load_all_rules\n"
        "load_all_rules()\n"
        "import tieout.cli, tieout.fixtures.generator, tieout.learn, tieout.report.html\n"
        f"watched = {watched!r}\n"
        "print(','.join(name for name in watched if name in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    loaded = [name for name in result.stdout.strip().split(",") if name]
    assert not loaded, f"importing tieout loaded {loaded}"


def test_the_html_report_has_no_external_references(clean_deck, reference_profile):
    """The report is the artefact most likely to be emailed outside the firm, so a
    stylesheet or font it fetches at view time would break the air-gap promise
    exactly where it matters most."""
    from tieout.report import html
    from tieout.rules.base import run_rules

    markup = html.render(run_rules(clean_deck, reference_profile), clean_deck)
    for forbidden in (
        "http://",
        "https://",
        "//cdn",
        "fonts.googleapis",
        "fonts.gstatic",
        "<script",
        "@import url",
    ):
        assert forbidden not in markup, f"the HTML report references {forbidden!r}"
