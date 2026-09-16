"""The one module in this repository that talks to a network.

Everything else in ``tieout_review`` is offline: the redaction, the extraction,
the term assembly and the parsing all run on the analyst's machine and are
testable without a key. Keeping the transport in a single small module is what
makes that claim checkable — ``tests/test_review_airgap.py`` asserts that no
other module in either package imports ``anthropic`` or anything like it.

On the key. It is read from the environment by the SDK, which is the behaviour
to prefer: it means this code never sees it, never logs it and cannot write it
anywhere. ``api_key`` exists as a parameter only because the local web UI has to
accept one typed into a form, and it is held for the life of the call and
nowhere else — not in a profile, not in a report, not in a cache, and not in
this object's ``repr``, which the field explicitly suppresses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

import anthropic
from anthropic.types import OutputConfigParam

__all__ = [
    "DEFAULT_MODEL",
    "AnthropicReviewClient",
    "ReviewClient",
    "ReviewClientError",
]

#: Adaptive thinking is the default on this model and is what this task wants: a
#: consistency sweep over a whole deck is exactly the kind of work where the
#: useful amount of reasoning varies per finding.
DEFAULT_MODEL: Final[str] = "claude-opus-5"

#: The environment variable the SDK resolves on its own.
API_KEY_ENV: Final[str] = "ANTHROPIC_API_KEY"


class ReviewClientError(RuntimeError):
    """The review could not be obtained. Carries a message fit to show a user."""


class ReviewClient(Protocol):
    """What the orchestration layer needs from a transport.

    A protocol rather than a base class so that the tests can substitute a
    deterministic stub, and so that nothing in :mod:`tieout_review.review`
    imports the SDK.
    """

    @property
    def model(self) -> str: ...

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> tuple[str, dict[str, int]]: ...


@dataclass
class AnthropicReviewClient:
    """A single non-streaming-in-effect request, streamed for timeout safety.

    Streaming is used with ``get_final_message`` rather than consuming events:
    the payload can be tens of thousands of characters and the response can be
    long, and a non-streamed request of that size is the classic way to collect
    a request timeout. Nothing here needs the tokens as they arrive.
    """

    model: str = DEFAULT_MODEL
    max_tokens: int = 16_000
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    timeout: float = 600.0
    max_retries: int = 2
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.api_key is None and not os.environ.get(API_KEY_ENV):
            raise ReviewClientError(
                f"no API key: set {API_KEY_ENV}, or run tieout itself, which needs none"
            )

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> tuple[str, dict[str, int]]:
        """Send one request and return ``(text, usage)``.

        ``schema`` is enforced by the API rather than only asked for in the
        prompt, so a malformed answer is not a failure mode that has to be
        handled downstream.

        Every failure mode is translated into :class:`ReviewClientError` with a
        message an analyst can act on. An unhandled SDK exception surfacing in a
        terminal — or worse, in the local UI — would be the kind of stack trace
        that leaks a request body.
        """
        client = anthropic.Anthropic(
            api_key=self.api_key,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        try:
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config=OutputConfigParam(
                    effort=self.effort,
                    format={"type": "json_schema", "schema": schema},
                ),
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise ReviewClientError("the API key was rejected") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ReviewClientError("the API key is not permitted to use this model") from exc
        except anthropic.RateLimitError as exc:
            raise ReviewClientError("rate limited; try again shortly") from exc
        except anthropic.APIConnectionError as exc:
            raise ReviewClientError(
                "could not reach the API. tieout itself needs no network; only this "
                "optional review layer does"
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ReviewClientError(f"the API returned {exc.status_code}") from exc

        text = "".join(block.text for block in message.content if block.type == "text")
        if not text.strip():
            raise ReviewClientError("the model returned no text")
        usage = {
            "input_tokens": getattr(message.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(message.usage, "output_tokens", 0) or 0,
        }
        return text, usage
