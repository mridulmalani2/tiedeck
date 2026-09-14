# TieOut

Deterministic, offline PowerPoint QA for investment banking decks, with zero-config client
onboarding derived from a single reference deck.

Upload one correct deck, get a working ruleset, check every deck after that.

```bash
tieout learn project_meridian_final.pptx --client acme
tieout check new_draft.pptx --client acme --format html --out report.html
```

TieOut makes no network calls, no LLM calls, and no telemetry. It runs air-gapped.

## Status

Under active construction. See `BUILD.md` for the specification this implements.

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

## Development

```bash
.venv/bin/python -m pytest          # tests
.venv/bin/python -m ruff check .    # lint
.venv/bin/python -m mypy            # types (strict)
```

A `SessionStart` hook in `.claude/` provisions `.venv` automatically in Claude Code sessions.
