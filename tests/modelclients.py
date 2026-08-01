"""Scripted stand-ins for the provider SDK clients.

Injecting here — at the SDK client the provider owns — is what lets a test
drive a whole turn through the real code: the provider's request projection
and response mapping both run, and only the network call is replaced. Each
client records the payload it was handed, so a test can assert what actually
reached the wire rather than what an intermediate seam was asked for.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any


class ScriptedModel:
    """Finite response script shared by the provider clients.

    A scripted entry is returned as-is, raised if it is an exception, or
    called with the payload if it is a callable (for a response that depends
    on the request, or one that parks so the turn can be interrupted).
    """

    def __init__(self, responses: Sequence[Any]) -> None:
        self._responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    async def respond(self, **payload: Any) -> Any:
        """Consume the next scripted entry, awaiting an async one."""
        response = self._next(**payload)
        return await response if inspect.isawaitable(response) else response

    def _next(self, **payload: Any) -> Any:
        self.payloads.append(payload)
        if not self._responses:
            raise AssertionError(
                f"model called {len(self.payloads)} times; the script supplied "
                f"{len(self.payloads) - 1}"
            )
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response(**payload) if callable(response) else response

    @property
    def call_count(self) -> int:
        """How many times the model was called, successes and failures alike."""
        return len(self.payloads)

    def assert_exhausted(self) -> None:
        """Every scripted response was consumed (no silently skipped round)."""
        assert not self._responses, (
            f"{len(self._responses)} scripted model responses were never requested"
        )

    @property
    def last_payload(self) -> dict[str, Any]:
        assert self.payloads, "the model was never called"
        return self.payloads[-1]


class ScriptedAnthropicClient(ScriptedModel):
    """Stands in for ``AsyncAnthropic`` (``client.messages.create(**payload)``)."""

    @property
    def messages(self) -> SimpleNamespace:
        return SimpleNamespace(create=self._create)

    async def _create(self, **payload: Any) -> Any:
        return await self.respond(**payload)

    async def close(self) -> None:
        """``AnthropicProvider.aclose`` closes the client it owns."""


class ScriptedGeminiClient(ScriptedModel):
    """Stands in for ``genai.Client`` (``client.aio.models.generate_content``)."""

    @property
    def aio(self) -> SimpleNamespace:
        return SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, **payload: Any) -> Any:
        return await self.respond(**payload)


def anthropic_reply(
    text: str | None = None,
    *,
    tool_calls: Sequence[tuple[str, dict[str, Any]]] = (),
    usage: Any = None,
) -> Any:
    """An Anthropic ``Message``-shaped reply, as the provider reads one.

    ``tool_calls`` are ``(name, arguments)`` pairs, given sequential ids.
    """
    from anthropic.types import TextBlock, ToolUseBlock

    content: list[Any] = [
        ToolUseBlock(type="tool_use", id=f"call-{index}", name=name, input=arguments)
        for index, (name, arguments) in enumerate(tool_calls, start=1)
    ]
    if text:
        content.append(TextBlock(type="text", text=text))
    return SimpleNamespace(
        stop_reason="tool_use" if tool_calls else "end_turn",
        content=content,
        usage=usage,
    )


def anthropic_usage(input_tokens: int, output_tokens: int) -> SimpleNamespace:
    """Anthropic-shaped usage (cache fields zeroed), in one place so the
    provider's field spelling is a single edit."""
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def gemini_reply(
    text: str | None = None,
    *,
    tool_calls: Sequence[tuple[str, dict[str, Any]]] = (),
    usage: Any = None,
) -> Any:
    """A Gemini ``GenerateContentResponse``-shaped reply, as the provider reads one."""
    from google.genai import types

    parts: list[types.Part] = [
        types.Part(
            function_call=types.FunctionCall(
                id=f"call-{index}", name=name, args=arguments
            )
        )
        for index, (name, arguments) in enumerate(tool_calls, start=1)
    ]
    if text:
        parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=usage,
    )
