"""Build native Gemini response bodies for adapter tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from google.genai import types


def _response_with_text(text: str) -> MagicMock:
    response = MagicMock()
    response.function_calls = []
    response.candidates = [
        MagicMock(
            content=types.Content(role="model", parts=[types.Part.from_text(text=text)])
        )
    ]
    return response


def _response_with_function_call(
    name: str, args: dict[str, str], call_id: str
) -> MagicMock:
    response = MagicMock()
    response.function_calls = [types.FunctionCall(name=name, args=args, id=call_id)]
    response.candidates = [
        MagicMock(
            content=types.Content(
                role="model",
                parts=[types.Part.from_function_call(name=name, args=args)],
            )
        )
    ]
    return response
