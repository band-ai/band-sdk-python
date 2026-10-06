"""Real google-genai responses for scripting the Google adapters' turns.

Gemini reads a ``GenerateContentResponse`` directly and Google ADK reads the
same object through ``LlmResponse.create``, so both adapters' tests script
turns from these builders. Every response carries ``SCRIPTED_USAGE``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from google.genai import types

from band.runtime.tools import AgentTools
from band.testing import FakeAgentTools

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
