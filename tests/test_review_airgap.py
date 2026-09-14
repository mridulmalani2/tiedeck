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
from tests.test_no_network import FORBIDDEN_MODULES

_CORE = Path(tieout.__file__).parent
_REVIEW = Path(tieout_review.__file__).parent

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


def test_the_review_package_is_outside_the_air_gapped_one():
    """Two top-level packages, not a subpackage.

    A subpackage would sit inside the walk's root and would have to be excluded
    from it by name, which is exactly the kind of exception that stops being
    noticed.
    """
    assert _REVIEW != _CORE
    assert _CORE not in _REVIEW.parents


def test_the_air_gap_walk_treats_the_review_package_as_forbidden():
    """So that a core module importing it is a test failure, not a review note."""
    assert "tieout_review" in FORBIDDEN_MODULES


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
    """Searched for structurally: no module in either package writes a key out."""
    for root in (_CORE, _REVIEW):
        for path in _modules(root):
            source = path.read_text(encoding="utf-8")
            assert "ANTHROPIC_API_KEY" not in source or path.name == _TRANSPORT
