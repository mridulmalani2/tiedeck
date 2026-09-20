"""The record of what left the machine.

Every other control in this package is preventative: the redactor replaces, the
hold refuses, :meth:`Redacted.verify` re-checks. None of them answer the
question compliance actually asks six months later, which is not "could
something have leaked" but "what did you send, when, and about which deck".
This module answers that one.

**What it records, and what it deliberately does not.** One line per
transmission: when, which deck by filename, which model, how much text, how many
terms were replaced, how many residuals the person accepted, the digest they
approved, and a SHA-256 of the transmitted text. Never the text. A log that
quotes the payload is a second copy of the thing the whole package exists to
protect, kept somewhere nobody is thinking about, in plaintext, for years. The
hash is enough: someone holding a payload can prove it is the one that went, and
someone holding only the log can prove nothing about its contents at all.

**When the line is written.** Immediately *before* the payload is handed to the
transport, not after the answer comes back. The two orderings fail differently
and the difference is the whole argument: log-after loses the record of a send
that crashed mid-flight, and log-before at worst records a send that the
transport rejected before opening a socket. A confidentiality log that
under-reports is broken. One that occasionally over-reports is merely
conservative, and a reader can tell which lines those are by the absence of any
answer to them.

**A transmission that cannot be recorded does not happen.** :func:`record` lets
the write fail loudly and :func:`tieout_review.review.send` does not catch it.
That is a real cost — a read-only working directory now blocks the review pass
where it used to work — and it is the right one: a best-effort audit trail is
worse than none, because it looks like evidence while being silent exactly when
the disk is full or the directory is locked. :data:`OUTBOUND_LOG_ENV` names the
way out for anyone who needs the log somewhere else.

The file is opened in append mode and no code here ever rewrites or truncates
it. That makes it append-only by *this* program's construction, which is what
can honestly be claimed; a filesystem that enforces immutability is the
operator's arrangement, not this module's.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tieout.profile.loader import profile_dir

__all__ = [
    "DEFAULT_OUTBOUND_LOG",
    "OUTBOUND_LOG_ENV",
    "OutboundLogError",
    "OutboundRecord",
    "outbound_log_path",
    "read_log",
    "record",
]

#: Overrides where the log is written, for a firm that keeps it on a share the
#: analyst cannot edit.
OUTBOUND_LOG_ENV: Final[str] = "TIEOUT_OUTBOUND_LOG"

#: Beside the profiles and suppression files, because that is the directory a
#: TieOut user already knows about and can hand over whole.
DEFAULT_OUTBOUND_LOG: Final[str] = "outbound.jsonl"

#: Readable and writable by the owner only. The log names deck files, which are
#: themselves identifying even when their contents never were.
_MODE: Final[int] = 0o600


class OutboundLogError(RuntimeError):
    """The transmission could not be recorded, so it was not made."""


def outbound_log_path() -> Path:
    override = os.environ.get(OUTBOUND_LOG_ENV)
    return Path(override) if override else profile_dir() / DEFAULT_OUTBOUND_LOG


@dataclass(frozen=True, slots=True)
class OutboundRecord:
    """One transmission, in the form the log keeps it.

    ``deck`` is the filename alone. The containing directory is often a matter
    number or a codename, and the log has no use for it.
    """

    sent_at: str
    deck: str
    model: str
    characters: int
    redactions: int
    residuals: int
    digest: str
    text_sha256: str

    @classmethod
    def now(
        cls,
        *,
        deck_path: str,
        model: str,
        characters: int,
        redactions: int,
        residuals: int,
        digest: str,
        text_sha256: str,
    ) -> OutboundRecord:
        return cls(
            sent_at=datetime.now(UTC).isoformat(timespec="seconds"),
            deck=Path(deck_path).name,
            model=model,
            characters=characters,
            redactions=redactions,
            residuals=residuals,
            digest=digest,
            text_sha256=text_sha256,
        )

    def as_line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)


def record(entry: OutboundRecord, path: str | Path | None = None) -> Path:
    """Append one line, and raise rather than let a send go unrecorded."""
    target = Path(path) if path is not None else outbound_log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        with target.open("a", encoding="utf-8") as handle:
            handle.write(entry.as_line() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not existed:
            _restrict(target)
    except OSError as exc:
        raise OutboundLogError(
            f"the outbound log at {target} could not be written ({exc.strerror or exc}); "
            "nothing has been sent. A review that cannot be recorded is not made. "
            f"Set {OUTBOUND_LOG_ENV} to a writable path."
        ) from exc
    return target


def read_log(path: str | Path | None = None) -> tuple[OutboundRecord, ...]:
    """Every transmission recorded so far, oldest first.

    Lines that do not parse are skipped rather than raising: the log is
    append-only and a truncated final line from a killed process must not make
    the whole history unreadable.
    """
    target = Path(path) if path is not None else outbound_log_path()
    if not target.exists():
        return ()
    entries: list[OutboundRecord] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            entries.append(OutboundRecord(**data))
        except TypeError:
            continue
    return tuple(entries)


def _restrict(target: Path) -> None:
    """Best effort, and only on a file this process just created.

    Failure is ignored on purpose: a share that does not implement POSIX modes
    is a reason to keep the log, not a reason to refuse the send. The mkdir and
    the write above are the parts that must succeed.
    """
    with contextlib.suppress(OSError):  # pragma: no cover - filesystem dependent
        target.chmod(_MODE)
