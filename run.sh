#!/usr/bin/env bash
#
# Start TieOut's local UI, setting everything up the first time.
#
#   ./run.sh                 # open the page on http://127.0.0.1:8765/
#   ./run.sh --port 9000     # any tieout-ui flag passes straight through
#   ./run.sh --no-open
#
# Safe to run every time. The first run builds a virtual environment and
# installs; later runs skip straight to starting the server, and reinstall only
# when pyproject.toml has changed since the last one.
#
# This exists because the alternative is six commands, one of which fails
# silently on the Python that ships with macOS. Someone evaluating a QA tool
# should be looking at their deck within a minute of cloning, not debugging a
# virtual environment.
set -euo pipefail

cd "$(dirname "$0")"

VENV=.venv
MARKER="$VENV/.tieout-ui-installed"
MIN_PYTHON="3.11"

say() { printf '%s\n' "$*" >&2; }
die() { printf '\nerror %s\n\n' "$*" >&2; exit 1; }

# -- 1. find an interpreter new enough -------------------------------------- #
#
# Searched newest first, and by name before falling back to bare `python3`,
# because the `python3` on a Mac is usually 3.9 even when a newer one is
# installed alongside it. That is the single most common way this fails.

usable() {
  command -v "$1" >/dev/null 2>&1 &&
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
      >/dev/null 2>&1
}

PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
  if usable "$candidate"; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  found=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      found="$("$candidate" -V 2>&1 || true)"
      break
    fi
  done
  say ""
  say "TieOut needs Python $MIN_PYTHON or newer."
  if [ -n "$found" ]; then
    say "The Python on this machine is $found, which is too old."
  else
    say "No Python was found on this machine."
  fi
  say ""
  say "  macOS          brew install python@3.12"
  say "  Debian/Ubuntu  sudo apt install python3.12 python3.12-venv"
  say "  Windows        install from python.org, then use run.ps1"
  say ""
  die "no usable Python. Nothing has been changed."
fi

# -- 2. the virtual environment --------------------------------------------- #

if [ -x "$VENV/bin/python" ] && ! usable "$VENV/bin/python"; then
  die "$VENV was built with a Python older than $MIN_PYTHON.
      Delete it and run this again:  rm -rf $VENV && ./run.sh"
fi

if [ ! -x "$VENV/bin/python" ]; then
  say "Setting up $VENV with $("$PYTHON" -V 2>&1). This happens once."
  if ! "$PYTHON" -m venv "$VENV" 2>/dev/null; then
    die "could not create a virtual environment.
      On Debian or Ubuntu the venv module ships separately:
          sudo apt install $(basename "$PYTHON")-venv"
  fi
fi

# -- 3. install, but only when something changed ---------------------------- #

if [ ! -f "$MARKER" ] || [ pyproject.toml -nt "$MARKER" ]; then
  say "Installing TieOut and its UI dependencies. This happens once."
  # A plain (non-editable) install, which every pip that can read a
  # pyproject.toml handles. Editable mode needs pip 21.3+, and the pip bundled
  # with an older Python is 21.2 -- it fails with a message about setuptools
  # that tells the reader nothing. Upgrading pip first is worth trying and not
  # worth failing over.
  "$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  if ! "$VENV/bin/python" -m pip install -q ".[ui]"; then
    die "the install failed. The output above says why."
  fi
  touch "$MARKER"
fi

# pip can report success and still leave no console script -- most often when
# run.sh has been copied somewhere without the rest of the project, so pip
# installed whatever it found in the working directory instead. Saying so beats
# the shell's "No such file or directory".
if [ ! -x "$VENV/bin/tieout-ui" ]; then
  die "installed, but $VENV/bin/tieout-ui is not there.
      Run this from inside a TieOut checkout -- the directory holding
      pyproject.toml and the tieout/ package. Currently: $PWD"
fi

# -- 4. go -------------------------------------------------------------------#
#
# exec, so Ctrl-C reaches the server rather than this script: stopping it is
# what deletes the decks it was holding.

exec "$VENV/bin/tieout-ui" "$@"
