"""Real google-genai responses for scripting the Google adapters' turns.

Gemini reads a ``GenerateContentResponse`` directly and Google ADK reads the
same object through ``LlmResponse.create``, so both adapters' tests script
turns from these builders and share ``ModelFailureCases``. Every response
carries ``SCRIPTED_USAGE``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from google.genai import types

from band.core.exceptions import ProviderRunError
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.core.simple_adapter import SimpleAdapter
from band.core.types import Emit
from band.runtime.tools import AgentTools, BandTool
from band.testing import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
    reported_failures,
)
from tests.adapters.usage_events import recorded_usage_payloads
from tests.framework_conformance.turnprobes import turn_input

ScriptedAdapter = Callable[..., Awaitable[SimpleAdapter[Any]]]

SCRIPTED_USAGE = types.GenerateContentResponseUsageMetadata(
    prompt_token_count=11, candidates_token_count=3
)

# Provider text that must reach only the agent log, never the room.
PROVIDER_DETAIL = "blocked: the prompt asked for the secret"


class PlatformSchemaFakeTools(FakeAgentTools):
    """A ``FakeAgentTools`` advertising the real platform tool schemas, so a
    scripted function call goes through the adapter's own tool bridge."""

    def get_openai_tool_schemas(self, **kwargs: Any) -> list[dict[str, Any]]:
        # Schema building reads the tool registry, never the REST link.
        return AgentTools("room-123", MagicMock()).get_openai_tool_schemas(**kwargs)


def tool_call(name: str, args: dict[str, Any]) -> types.GenerateContentResponse:
    return _candidate(
        types.Content(
            role="model", parts=[types.Part.from_function_call(name=name, args=args)]
        ),
        types.FinishReason.STOP,
    )


def text_reply(
    text: str, finish: types.FinishReason = types.FinishReason.STOP
) -> types.GenerateContentResponse:
    return _candidate(
        types.Content(role="model", parts=[types.Part.from_text(text=text)]), finish
    )


def stopped(reason: types.FinishReason) -> types.GenerateContentResponse:
    """A candidate with no content, ended for ``reason``."""
    return _candidate(None, reason, finish_message=PROVIDER_DETAIL)


def prompt_blocked(reason: types.BlockedReason) -> types.GenerateContentResponse:
    """No candidates: the prompt itself was refused."""
    return types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=reason, block_reason_message=PROVIDER_DETAIL
        ),
        usage_metadata=SCRIPTED_USAGE,
    )


def no_candidates() -> types.GenerateContentResponse:
    return types.GenerateContentResponse(usage_metadata=SCRIPTED_USAGE)


def _candidate(
    content: types.Content | None,
    finish: types.FinishReason,
    *,
    finish_message: str | None = None,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=content, finish_reason=finish, finish_message=finish_message
            )
        ],
        usage_metadata=SCRIPTED_USAGE,
    )


class ModelFailureCases:
    """Turn cases for an adapter whose model returns a failed run as data. A
    subclass sets ``PROVIDER`` and provides a ``scripted_adapter`` fixture that
    builds a started adapter playing the given responses in order."""

    PROVIDER: ClassVar[str]

    @pytest.mark.parametrize(
        ("script", "code"),
        [
            pytest.param([stopped(types.FinishReason.SAFETY)], "SAFETY", id="safety"),
            pytest.param(
                [prompt_blocked(types.BlockedReason.PROHIBITED_CONTENT)],
                "PROHIBITED_CONTENT",
                id="prompt-blocked",
            ),
            pytest.param(
                [
                    tool_call(BandTool.CREATE_CHATROOM, {}),
                    stopped(types.FinishReason.SAFETY),
                ],
                "SAFETY",
                id="safety-after-real-work",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_reports_the_failure_code_without_the_provider_text(
        self,
        scripted_adapter: ScriptedAdapter,
        script: list[types.GenerateContentResponse],
        code: str,
    ) -> None:
        await self.assert_turn_fails_with(scripted_adapter, script, code)

    @pytest.mark.asyncio
    async def test_empty_normal_finish_is_only_a_missing_reply(
        self, scripted_adapter: ScriptedAdapter
    ) -> None:
        adapter = await scripted_adapter(stopped(types.FinishReason.STOP))
        tools = PlatformSchemaFakeTools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.asyncio
    async def test_failed_run_still_emits_its_usage(
        self, scripted_adapter: ScriptedAdapter
    ) -> None:
        adapter = await scripted_adapter(
            stopped(types.FinishReason.SAFETY), emit={Emit.USAGE}
        )
        tools = PlatformSchemaFakeTools()

        with pytest.raises(ProviderRunError):
            await adapter.on_event(turn_input(tools))

        [usage] = recorded_usage_payloads(tools)
        assert usage["input_tokens"] == SCRIPTED_USAGE.prompt_token_count

    async def assert_turn_fails_with(
        self,
        scripted_adapter: ScriptedAdapter,
        script: list[types.GenerateContentResponse],
        code: str,
    ) -> None:
        adapter = await scripted_adapter(*script)
        tools = PlatformSchemaFakeTools()

        with pytest.raises(ProviderRunError):
            await adapter.on_event(turn_input(tools))

        assert reported_failures(tools) == [
            {
                "provider": self.PROVIDER,
                "code": code,
                "message": GENERIC_PROVIDER_FAILURE_MESSAGE,
                "detail": None,
            }
        ]
        posted = [*tools.chat, *(event["content"] for event in tools.events_sent)]
        assert not any(PROVIDER_DETAIL in content for content in posted)
