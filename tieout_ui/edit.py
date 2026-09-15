"""The edits the UI offers: dropping a derived fact, and retyping one.

Separate from the server because the view layer has to ask the same question
before it draws the control. Offering a control for a field that cannot take it
is how you get a click that does nothing, or — as it did — a 500.

Dropping is always safe: the rule that read the field stops running. Retyping is
not, and the danger is specific — a value the client's own approved deck does
not support turns the tool against their own material, and everything downstream
treats the profile as ground truth. That is a judgement to put in front of the
person making it, which is what the provenance note, the lock and the warning
beside the controls are for. It is not a reason to make the house style
read-only: refusing the edit only moves it into a text editor, where nothing
validates it and nothing records that a person made it.
"""

from __future__ import annotations

import re
import types
import typing
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from tieout.profile.schema import EXEMPT, NotLearned, Profile

__all__ = ["EditRejected", "apply_edits", "can_clear", "clear", "drop", "editable"]


def drop(profile: Profile, paths: list[str]) -> list[str]:
    """Remove derived facts the person disagreed with.

    Dropping is the safe edit: the rule that read the field simply stops
    running, and `not_learned` records that a person decided so, so nothing
    downstream can mistake the silence for a measurement. :func:`apply_edits`
    is the other one, and carries the risk this does not.
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


# --------------------------------------------------------------------------------------
# Editing a derived value
# --------------------------------------------------------------------------------------
#
# Dropping alone turned out to be too blunt in practice. A house style is not
# only a set of claims to accept or reject: a margin is 30pt because someone
# decided it should be, and an analyst who can see that the reference deck was
# measured at 28.4pt wants to round it, not delete it. Refusing that pushed the
# work into a text editor, which is worse -- the YAML has no validation in front
# of it and no record of who changed what.
#
# So editing is offered, with the original objection answered rather than
# ignored: every hand-set value is validated against the schema before it lands,
# recorded in the provenance as having been set by a person rather than
# measured, and locked so a later `learn --add` cannot quietly overwrite it. The
# page says plainly, next to the controls, that a value the client's own
# approved deck does not support will report their material as wrong. That is a
# judgement the person making it can see; a silent refusal to let them make it
# is not.


class EditRejected(ValueError):
    """An edit that could not be applied, with the reason a person needs."""


def editable(profile: Profile, path: str) -> list[dict[str, Any]]:
    """The controls the page should draw for ``path``, innermost first.

    Asked by the view for the same reason :func:`can_clear` is: the page must
    not offer a control that would not take. A scalar yields one control; a
    field holding a model -- a margin set, a size band, a logo box -- yields one
    per editable member, because "30 / 30 / 24 / 30pt" is four decisions and
    editing it as one string is how you get a typo in the third number.

    An empty list means the value is not editable, which is the honest answer
    for a palette, a canon-term map or anything else whose shape a form cannot
    carry.
    """
    resolved = _resolve(profile, path)
    if resolved is None:
        return []
    owner, leaf = resolved
    try:
        current = _read(owner, leaf)
    except (KeyError, IndexError, AttributeError):
        return []

    # "exempt" is a sentinel standing in for a whole logo box, not a value with
    # a meaningful other spelling. A text field containing it would reject every
    # edit a person could make in it.
    if current == EXEMPT:
        return []

    if isinstance(current, BaseModel):
        controls = []
        for name, field in type(current).model_fields.items():
            member = getattr(current, name, None)
            spec = _control(field.annotation, member)
            if spec is not None:
                controls.append({
                    **spec,
                    "path": f"{path}.{name}",
                    "label": _label(name),
                    "value": _form_value(member),
                })
        return controls

    spec = _control(_annotation_of(owner, leaf), current)
    if spec is None:
        return []
    return [{**spec, "path": path, "label": "", "value": _form_value(current)}]


def apply_edits(
    profile: Profile, edits: dict[str, Any]
) -> tuple[list[str], list[dict[str, str]]]:
    """Set hand-typed values, returning what took and what did not.

    Rejections are returned rather than raised. One bad number in a form of
    twelve should not discard the other eleven, and the person needs to be told
    which one it was.
    """
    applied: list[str] = []
    rejected: list[dict[str, str]] = []

    for path in sorted(edits):
        raw = edits[path]
        resolved = _resolve(profile, path)
        if resolved is None:
            rejected.append({"path": path, "reason": "no such field in this profile"})
            continue
        owner, leaf = resolved
        try:
            current = _read(owner, leaf)
        except (KeyError, IndexError, AttributeError):
            rejected.append({"path": path, "reason": "no such field in this profile"})
            continue

        spec = _control(_annotation_of(owner, leaf), current)
        if spec is None:
            rejected.append({"path": path, "reason": "this field cannot be typed into"})
            continue

        try:
            value = _coerce(raw, spec)
        except EditRejected as exc:
            rejected.append({"path": path, "reason": str(exc)})
            continue

        if value == current:
            continue

        try:
            _write(owner, leaf, value)
        except (ValidationError, ValueError, TypeError) as exc:
            rejected.append({"path": path, "reason": _first_line(exc)})
            continue

        applied.append(path)
        profile.set_provenance(path, f"set by hand in the UI: {_shown(value)}", "high")
        if path not in profile.locks:
            profile.locks.append(path)

    return applied, rejected


# -- resolving a path ------------------------------------------------------- #


def _resolve(profile: Profile, path: str) -> tuple[Any, str] | None:
    """The container holding ``path``'s last segment, and that segment."""
    parts = path.split(".")
    target: Any = profile
    for part in parts[:-1]:
        if isinstance(target, dict):
            target = target.get(part)
        elif isinstance(target, list):
            try:
                target = target[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            target = getattr(target, part, None)
        if target is None:
            return None
    return target, parts[-1]


def _read(owner: Any, leaf: str) -> Any:
    if isinstance(owner, dict):
        return owner[leaf]
    if isinstance(owner, list):
        return owner[_index(leaf)]
    return getattr(owner, leaf)


def _write(owner: Any, leaf: str, value: Any) -> None:
    if isinstance(owner, dict):
        owner[leaf] = value
    elif isinstance(owner, list):
        owner[_index(leaf)] = value
    else:
        setattr(owner, leaf, value)


def _index(leaf: str) -> int:
    """``leaf`` as a list position, or a miss.

    Not every path segment under a list is one. ``layout.recurring`` is a list,
    but the view names its members by key -- ``layout.recurring.name:footnote``
    -- so this is asked about segments that were never indices. Raising KeyError
    rather than ValueError puts the answer in the same bucket as every other
    "no such field", which is what both callers already handle.
    """
    try:
        return int(leaf)
    except ValueError as exc:
        raise KeyError(leaf) from exc


def _annotation_of(owner: Any, leaf: str) -> Any:
    """The declared type of ``leaf``, or None when the container is untyped.

    A dict or list entry has no per-key annotation to consult, so the control is
    inferred from the value actually there. That is weaker than reading the
    schema, but the alternative -- resolving the container's value type through
    its own annotation -- buys nothing here: every such container in the profile
    holds either a model or a scalar of an obvious kind.
    """
    if isinstance(owner, dict | list):
        return None
    field = getattr(type(owner), "model_fields", {}).get(leaf)
    return None if field is None else field.annotation


# -- describing a control --------------------------------------------------- #


def _control(annotation: Any, current: Any) -> dict[str, Any] | None:
    """How ``annotation`` (or failing that, ``current``) should be typed into."""
    options = _literal_options(annotation)
    if options:
        return {"type": "choice", "options": options, "optional": _is_optional(annotation)}

    base = _unwrapped(annotation)
    optional = _is_optional(annotation)

    if base is bool or isinstance(current, bool):
        return {"type": "choice", "options": ["true", "false"], "optional": optional}
    if base in (int, float):
        return {"type": "number", "integer": base is int, "optional": optional}
    if base is str:
        return {"type": "text", "optional": optional}
    # ``==`` rather than ``is``: parameterised generics are not interned, so
    # ``list[str] is list[str]`` is False and every list read as numeric.
    if base == list[str]:
        return {"type": "list", "optional": optional}
    if base in (list[float], list[int]):
        return {"type": "numbers", "optional": optional}

    if annotation is None:  # inferred from the value in an untyped container
        if isinstance(current, bool):
            return {"type": "choice", "options": ["true", "false"], "optional": False}
        if isinstance(current, int | float):
            return {"type": "number", "integer": isinstance(current, int), "optional": False}
        if isinstance(current, str):
            return {"type": "text", "optional": False}
        if isinstance(current, list) and all(isinstance(v, str) for v in current):
            return {"type": "list", "optional": False}
        if isinstance(current, list) and all(isinstance(v, int | float) for v in current):
            return {"type": "numbers", "optional": False}
    return None


def _unwrapped(annotation: Any) -> Any:
    """``X`` from ``X | None``; the annotation itself otherwise."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        rest = [a for a in typing.get_args(annotation) if a is not type(None)]
        return rest[0] if len(rest) == 1 else None
    return annotation


def _literal_options(annotation: Any) -> list[str]:
    for candidate in (annotation, _unwrapped(annotation)):
        if typing.get_origin(candidate) is typing.Literal:
            return [str(a) for a in typing.get_args(candidate)]
    return []


# -- coercing what the form sent -------------------------------------------- #


def _coerce(raw: Any, spec: dict[str, Any]) -> Any:
    kind = spec["type"]
    text = "" if raw is None else str(raw).strip()

    if kind in ("list", "numbers"):
        parts = [p.strip() for p in text.replace("\n", ",").split(",") if p.strip()]
        if kind == "list":
            return parts
        try:
            return [float(p) for p in parts]
        except ValueError as exc:
            raise EditRejected("every entry has to be a number") from exc

    if not text:
        if spec.get("optional"):
            return None
        raise EditRejected("this field cannot be left empty")

    if kind == "number":
        try:
            value = float(text)
        except ValueError as exc:
            raise EditRejected(f"{text!r} is not a number") from exc
        return int(value) if spec.get("integer") else value

    if kind == "choice":
        if text not in spec["options"]:
            raise EditRejected(f"{text!r} is not one of {', '.join(spec['options'])}")
        if text in ("true", "false"):
            return text == "true"
        return text

    return text


def _form_value(value: Any) -> str:
    """``value`` as the form field should arrive holding it.

    The inverse of :func:`_coerce`, so that a control rendered from this and
    submitted untouched round-trips to the same value and is never recorded as a
    hand-set edit.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(_form_value(item) for item in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _label(name: str) -> str:
    return name.removesuffix("_pt").removesuffix("_delta_e").replace("_", " ")


def _shown(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "(empty)"
    return "(cleared)" if value is None else str(value)


#: Pydantic appends its machine-readable tail to the human sentence.
_PYDANTIC_TAIL: Final[re.Pattern[str]] = re.compile(r"\s*\[type=.*$")


def _first_line(exc: Exception) -> str:
    """The sentence a person can act on, out of a pydantic error report.

    Taking the first line gave "1 validation error for BrandProfile", which
    names neither the field nor what was wrong with it. The line worth showing
    is the validator's own message, which pydantic prefixes with "Value error,"
    and follows with a bracketed machine-readable tail.
    """
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    for line in lines:
        if line.startswith("Value error,"):
            return _PYDANTIC_TAIL.sub("", line.removeprefix("Value error,").strip())
    for line in lines[1:]:
        if not line.startswith(("For further information", "[")):
            return _PYDANTIC_TAIL.sub("", line)
    return "the value was refused by the schema"
