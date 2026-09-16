"""The transport, with the SDK stubbed out.

Nothing here makes a request. What is worth testing is the translation layer:
every SDK failure has to become a :class:`ReviewClientError` carrying a sentence
an analyst can act on, because an unhandled SDK exception surfacing in a
terminal — or worse, in the local web UI — is the kind of stack trace that
prints a request body.
"""

from __future__ import annotations

import types

import pytest

from tieout_review.client import API_KEY_ENV, AnthropicReviewClient, ReviewClientError


class _Usage:
    input_tokens = 1234
    output_tokens = 56


class _Block:
    def __init__(self, kind: str, text: str = "") -> None:
        self.type = kind
        self.text = text


class _Message:
    def __init__(self, blocks: list[_Block]) -> None:
        self.content = blocks
        self.usage = _Usage()


class _Stream:
    def __init__(self, message: _Message) -> None:
        self._message = message

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_final_message(self) -> _Message:
        return self._message


def _fake_sdk(*, message: _Message | None = None, raises: str | None = None):
    """An ``anthropic`` stand-in.

    The error classes are plain exceptions defined here rather than real SDK
    ones: the code under test reaches them through the module attribute, so
    patching the module is enough, and constructing a real ``APIStatusError``
    needs a response object this test has no business building.
    """
    errors = {
        name: type(name, (Exception,), {})
        for name in (
            "AuthenticationError",
            "PermissionDeniedError",
            "RateLimitError",
            "APIConnectionError",
            "APIStatusError",
        )
    }
    calls: list[dict[str, object]] = []

    class _Messages:
        def stream(self, **kwargs: object) -> _Stream:
            calls.append(kwargs)
            if raises is not None:
                instance = errors[raises]()
                if raises == "APIStatusError":
                    instance.status_code = 503
                raise instance
            assert message is not None
            return _Stream(message)

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            self.messages = _Messages()

    module = types.SimpleNamespace(Anthropic=_Client, **errors)
    return module, errors, calls


@pytest.fixture(autouse=True)
def _a_key_exists(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "sk-ant-test")


def _patch(monkeypatch, module) -> None:
    import tieout_review.client as transport

    monkeypatch.setattr(transport, "anthropic", module)


def test_a_successful_call_returns_the_text_and_the_usage(monkeypatch):
    module, _, calls = _fake_sdk(
        message=_Message([_Block("thinking"), _Block("text", '{"findings": []}')])
    )
    _patch(monkeypatch, module)
    text, usage = AnthropicReviewClient().complete("sys", "user", {"type": "object"})
    assert text == '{"findings": []}'
    assert usage == {"input_tokens": 1234, "output_tokens": 56}
    assert len(calls) == 1


def test_the_request_asks_for_adaptive_thinking_and_the_schema(monkeypatch):
    module, _, calls = _fake_sdk(message=_Message([_Block("text", "{}")]))
    _patch(monkeypatch, module)
    schema: dict[str, object] = {"type": "object", "properties": {}}
    AnthropicReviewClient().complete("sys", "user", schema)
    (kwargs,) = calls
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert kwargs["system"] == "sys"


def test_thinking_blocks_are_not_mistaken_for_the_answer(monkeypatch):
    module, _, _ = _fake_sdk(
        message=_Message([_Block("thinking", "deliberating"), _Block("text", "answer")])
    )
    _patch(monkeypatch, module)
    text, _ = AnthropicReviewClient().complete("sys", "user", {})
    assert text == "answer"


def test_an_empty_response_is_an_error_rather_than_an_empty_report(monkeypatch):
    """Reporting "no findings" because the model said nothing would be a lie."""
    module, _, _ = _fake_sdk(message=_Message([_Block("text", "   ")]))
    _patch(monkeypatch, module)
    with pytest.raises(ReviewClientError):
        AnthropicReviewClient().complete("sys", "user", {})


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("AuthenticationError", "rejected"),
        ("PermissionDeniedError", "not permitted"),
        ("RateLimitError", "rate limited"),
        ("APIConnectionError", "could not reach"),
        ("APIStatusError", "503"),
    ],
)
def test_every_sdk_failure_becomes_a_readable_error(monkeypatch, error_name, expected):
    module, _, _ = _fake_sdk(raises=error_name)
    _patch(monkeypatch, module)
    with pytest.raises(ReviewClientError) as excinfo:
        AnthropicReviewClient().complete("sys", "user", {})
    assert expected in str(excinfo.value).lower()


def test_a_connection_failure_says_the_core_tool_needs_no_network(monkeypatch):
    """The most likely failure on a locked-down desk, and the moment to say that
    everything except this layer works offline."""
    module, _, _ = _fake_sdk(raises="APIConnectionError")
    _patch(monkeypatch, module)
    with pytest.raises(ReviewClientError) as excinfo:
        AnthropicReviewClient().complete("sys", "user", {})
    assert "needs no network" in str(excinfo.value)


def test_an_explicit_key_is_accepted_without_the_environment(monkeypatch):
    """The local UI has to accept one typed into a form."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    module, _, _ = _fake_sdk(message=_Message([_Block("text", "{}")]))
    _patch(monkeypatch, module)
    text, _ = AnthropicReviewClient(api_key="sk-ant-typed").complete("sys", "user", {})
    assert text == "{}"


def test_the_model_defaults_to_the_current_opus(monkeypatch):
    module, _, calls = _fake_sdk(message=_Message([_Block("text", "{}")]))
    _patch(monkeypatch, module)
    client = AnthropicReviewClient()
    client.complete("sys", "user", {})
    assert calls[0]["model"] == client.model
