"""Binding an approval to the exact payload it was given for.

The hold in :mod:`tieout_review.review` is sound in one process and unbound
across two. ``prepare()`` builds a payload and a residual list, a person reads
the list and agrees to it, and ``send()`` transmits. In the CLI those three
things happen microseconds apart on one object and nothing can get between
them. Over HTTP they are two requests, and the second one carries only the
word "approved". The server has no way to tell whether that word refers to the
list it displayed, to a list from an hour ago, or to no list at all.

So the approval carries a digest instead of a boolean, and the digest covers
*both halves of what was shown*:

* the **residual list** — what the person was asked to rule on, and
* the **payload text** — what will actually leave the machine.

Covering the text is not belt-and-braces. A digest over the residual list alone
would hash an empty list to one constant value for every deck that redacts
cleanly, so an approval granted for one clean deck would validate a send of any
other. "Nothing outstanding" would become the way around the check. The text
hash is what stops that, and the empty-residual case is the one to keep in mind
when reading :func:`residual_digest`.

Nothing here is a secret and nothing here needs to be. The digest is not a
signature: someone who can post to the loopback API can also call
``/api/redact`` and read the digest back. What it defends against is *drift* —
a deck, a blocklist or a forbidden-terms list that changed between the approval
and the send — which is the failure that happens by accident, repeatedly, and
leaves no trace. Forgery by someone already inside the loopback boundary is the
session token's job, not this module's.

The version tag is part of the hashed material on purpose. If the canonical
form ever changes, every previously-issued digest stops matching rather than
silently colliding with a payload it never described.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Final

from tieout_review.redact import Residual

__all__ = ["DIGEST_VERSION", "residual_digest", "short_digest", "text_digest"]

#: Bumped whenever the canonical form below changes, so an old digest fails
#: closed instead of matching something it was never computed over.
DIGEST_VERSION: Final[int] = 1

#: Enough to read aloud over a desk and still be specific: 64 bits of the hash.
_SHORT: Final[int] = 16


def text_digest(text: str) -> str:
    """SHA-256 of the payload exactly as it would be transmitted.

    Also what the outbound log records, so a line in that log can be matched
    against a payload someone still has without the log ever holding a copy of
    it.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def residual_digest(text: str, residuals: Sequence[Residual]) -> str:
    """The value an approval has to name, over the payload and its residuals.

    Residuals are sorted into a canonical order rather than taken as they came.
    The redactor's ordering is stable today, but a digest that depends on it
    would make an unrelated change to the scan order look, to whoever was
    holding an approval, exactly like the deck being tampered with.
    """
    canonical = {
        "version": DIGEST_VERSION,
        "text": text_digest(text),
        "residuals": sorted(
            [residual.text, residual.reason, residual.occurrences, residual.first_line]
            for residual in residuals
        ),
    }
    encoded = json.dumps(
        canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def short_digest(value: str | None) -> str:
    """A digest as it appears in a message a person has to act on."""
    if not value:
        return "no digest"
    return value[:_SHORT]
