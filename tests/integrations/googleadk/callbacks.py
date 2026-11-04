"""Build native callback inputs for isolated batch-state checks."""

from __future__ import annotations

from unittest.mock import Mock

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types


def context(invocation_id: str = "current") -> CallbackContext:
    ctx = Mock(spec=CallbackContext)
    ctx.invocation_id = invocation_id
    return ctx


def response(*names: str, partial: bool = False) -> LlmResponse:
    return LlmResponse(
        partial=partial,
        content=types.Content(
            role="model",
            parts=[types.Part.from_function_call(name=name, args={}) for name in names],
        ),
    )
