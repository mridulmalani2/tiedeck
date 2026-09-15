"""The one edit the UI offers: dropping a derived fact.

Separate from the server because the view layer has to ask the same question
before it draws the control. Offering a checkbox for a field that cannot be
dropped is how you get a click that does nothing, or — as it did — a 500.

Dropping is the only edit on purpose. Retyping a value into a form is how you
get a profile the client's own approved deck would fail, and everything
downstream treats the profile as ground truth.
"""

from __future__ import annotations

import types
import typing
from typing import Any

from pydantic import ValidationError

from tieout.profile.schema import NotLearned, Profile

__all__ = ["can_clear", "clear", "drop"]


def drop(profile: Profile, paths: list[str]) -> list[str]:
    """Remove derived facts the person disagreed with.

    Dropping is the only edit offered. Retyping a value in a form is how you get
    a profile the reference deck itself would fail, and then a client whose own
    approved deck is reported as wrong. Dropping is always safe: the rule that
    read the field stops running, and `not_learned` records that a person
    decided so.
    """
    dropped: list[str] = []
    for path in paths:
        if clear(profile, path):
            dropped.append(path)
            profile.not_learned.append(
                NotLearned(key=path, reason="dropped in the UI: not a house rule")
            )
            if path not in profile.locks:
                profile.locks.append(path)
    return dropped


def clear(profile: Profile, path: str) -> bool:
    """Remove one derived fact, by whatever "remove" means for its field.

    The naive version assigned ``None`` to every scalar. The schema validates on
    assignment, so dropping a non-optional field — a tolerance, a share — raised
    a ``ValidationError`` out of the request handler: a 500 where the UI had
    invited the click. (The validator is why it was a crash and not a profile
    that reloads as `null` and fails later, which would have been worse.)

    So "drop" is resolved per field kind: a dict entry is removed, a list is
    emptied, an optional field becomes ``None``, and a required field with a
    default goes back to that default — which is the honest reading, since a
    tolerance is a parameter rather than a claim about the client. A required
    field with no default cannot be dropped at all, and says so by returning
    False rather than by raising.
    """
    parts = path.split(".")
    target: Any = profile
    for part in parts[:-1]:
        if isinstance(target, dict):
            target = target.get(part)
        elif isinstance(target, list):
            try:
                target = target[int(part)]
            except (ValueError, IndexError):
                return False
        else:
            target = getattr(target, part, None)
        if target is None:
            return False

    leaf = parts[-1]
    if isinstance(target, dict):
        return target.pop(leaf, None) is not None
    if isinstance(target, list):
        try:
            target.pop(int(leaf))
        except (ValueError, IndexError):
            return False
        return True
    if not hasattr(target, leaf):
        return False

    try:
        setattr(target, leaf, _emptied(target, leaf))
    except (ValidationError, ValueError, TypeError):
        return False
    return True


def _emptied(model: Any, field_name: str) -> Any:
    """What ``field_name`` should hold once a person has dropped it."""
    current = getattr(model, field_name)
    if isinstance(current, list):
        return []
    if isinstance(current, dict):
        return {}
    field = getattr(type(model), "model_fields", {}).get(field_name)
    if field is None:
        return None
    if _is_optional(field.annotation):
        return None
    if field.is_required():
        raise ValueError(f"{field_name} is required and has no default to fall back to")
    return field.get_default(call_default_factory=True)


def _is_optional(annotation: Any) -> bool:

    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return type(None) in typing.get_args(annotation)
    return False



def can_clear(profile: Profile, path: str) -> bool:
    """Whether :func:`clear` would do anything, without doing it.

    Asked by the view so the UI only offers the control where it means
    something. Implemented as a dry run against a copy rather than as a second
    set of rules, because two implementations of "is this droppable" would
    eventually disagree and the disagreement would show up as a dead checkbox.
    """
    return clear(profile.model_copy(deep=True), path)
