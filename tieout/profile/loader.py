"""Reading profiles, suppression files and the shipped default.

Provenance is read from the generated ``provenance`` block rather than from the
``why:`` comments beside each value. The comments are what a person reads; the
block is what the rules cite. See the note in :mod:`tieout.learn.emit` for why
recovering it from the comments alone was abandoned.

A profile with no ``provenance`` block still loads, and one written by an older
version still loads: a missing note means a finding reports no provenance, which
is honest, rather than the wrong one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from tieout.learn.emit import PROVENANCE_PREFIX
from tieout.profile.schema import Profile, SuppressionFile

#: Where profiles live, relative to the working directory.
DEFAULT_PROFILE_DIR: Final[str] = "profiles"

#: Overrides the directory, for a firm that keeps profiles on a shared drive.
PROFILE_DIR_ENV: Final[str] = "TIEOUT_PROFILE_DIR"

class ProfileError(RuntimeError):
    """Raised when a profile cannot be read or does not validate."""


def profile_dir() -> Path:
    return Path(os.environ.get(PROFILE_DIR_ENV) or DEFAULT_PROFILE_DIR)


def profile_path(client: str) -> Path:
    return profile_dir() / f"{client}.yaml"


def suppression_path(client: str) -> Path:
    return profile_dir() / f"{client}.suppress.yaml"


def _yaml() -> YAML:
    handler = YAML()
    handler.preserve_quotes = True
    return handler


def load_raw(path: str | Path) -> CommentedMap:
    """Load a profile as a round-trip document, comments intact."""
    target = Path(path)
    if not target.exists():
        raise ProfileError(f"no profile at {target}")
    try:
        data = _yaml().load(target.read_text(encoding="utf-8"))
    except YAMLError as exc:
        raise ProfileError(f"{target} is not valid YAML: {exc}") from exc
    if not isinstance(data, CommentedMap):
        raise ProfileError(f"{target} does not contain a profile mapping")
    return data


def load(path: str | Path) -> Profile:
    """Load and validate a profile, restoring its provenance from comments."""
    target = Path(path)
    payload: dict[str, Any] = _plain(load_raw(target))
    try:
        return Profile.model_validate(payload)
    except ValidationError as exc:
        raise ProfileError(f"{target} is not a valid profile:\n{exc}") from exc


def load_for_client(client: str, explicit: str | Path | None = None) -> Profile:
    """Resolve ``--client`` and an optional ``--profile`` to one profile."""
    if explicit is not None:
        return load(explicit)
    candidate = profile_path(client)
    if candidate.exists():
        return load(candidate)
    raise ProfileError(
        f"no profile for client {client!r} at {candidate}. Run "
        f"`tieout learn REFERENCE.pptx --client {client}` first, or pass "
        f"--profile PATH."
    )


def load_suppressions(client: str, path: str | Path | None = None) -> SuppressionFile:
    """Load accepted findings. A missing file is not an error."""
    target = Path(path) if path is not None else suppression_path(client)
    if not target.exists():
        return SuppressionFile(client=client)
    try:
        data = _yaml().load(target.read_text(encoding="utf-8"))
    except YAMLError as exc:
        raise ProfileError(f"{target} is not valid YAML: {exc}") from exc
    if data is None:
        return SuppressionFile(client=client)
    try:
        return SuppressionFile.model_validate(_plain(data))
    except ValidationError as exc:
        raise ProfileError(f"{target} is not a valid suppression file:\n{exc}") from exc


def write_suppressions(suppressions: SuppressionFile, path: str | Path) -> Path:
    """Write a suppression file, merging repeat acceptances into one entry.

    The count matters: section 8.7 says three or more acceptances of the same
    rule is a signal the rule is miscalibrated, and that suggestion can only be
    made if the count survives.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = _yaml()
    payload = suppressions.model_dump(mode="json")
    import io

    stream = io.StringIO()
    stream.write(
        "# Findings accepted with `tieout check --accept RULE@slideN`.\n"
        "# A rule accepted three or more times is probably miscalibrated: run\n"
        "# `tieout learn --add` with the deck that provoked it so the evidence\n"
        "# is folded into the profile instead of being suppressed.\n"
    )
    handler.dump(payload, stream)
    target.write_text(stream.getvalue(), encoding="utf-8")
    return target


# --------------------------------------------------------------------------------------
# Provenance recovery
# --------------------------------------------------------------------------------------


def read_comment_notes(data: CommentedMap) -> dict[str, str]:
    """Top-level ``why:`` notes, for ``tieout profile show`` on a hand-edited file.

    Only the top level, because that is the one nesting depth at which YAML's
    habit of attaching a leading comment to the preceding sibling cannot
    misattribute a note to a different section.
    """
    notes: dict[str, str] = {}
    for key in data:
        token = data.ca.items.get(key)
        if not token:
            continue
        for entry in token:
            if entry is None:
                continue
            for comment in entry if isinstance(entry, list) else [entry]:
                for line in str(getattr(comment, "value", "")).splitlines():
                    stripped = line.strip().lstrip("#").strip()
                    if stripped.startswith(PROVENANCE_PREFIX):
                        notes[str(key)] = stripped[len(PROVENANCE_PREFIX) :].strip()
    return notes


def _plain(value: Any) -> Any:
    """Strip ruamel's comment-carrying wrappers, leaving plain containers."""
    if isinstance(value, (CommentedMap, dict)):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (CommentedSeq, list)):
        return [_plain(item) for item in value]
    return value
