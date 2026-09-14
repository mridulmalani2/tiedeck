#!/usr/bin/env bash
# SessionStart hook: make `pytest`, `ruff` and `mypy` runnable immediately in a
# fresh Claude Code session. Idempotent and safe to re-run.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

# Only reinstall when the dependency declaration is newer than the marker.
MARKER=.venv/.tieout-deps-installed
if [ ! -f "$MARKER" ] || [ pyproject.toml -nt "$MARKER" ]; then
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -e ".[dev]"
  touch "$MARKER"
fi

echo "tieout: .venv ready. Run tests with .venv/bin/python -m pytest"
