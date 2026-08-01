"""Removed constructor aliases must raise guided TypeErrors."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"api_key": "sk"}, "provider_key"),
        ({"anthropic_api_key": "sk"}, "provider_key"),
        ({"max_tokens": 10}, "max_output_tokens"),
        ({"prompt": "hi"}, "instructions"),
        ({"enable_memory_tools": True}, "features"),
    ],
)
def test_anthropic_removed_kwargs(kwargs: dict, match: str) -> None:
    from band.adapters.anthropic import AnthropicAdapter

    with pytest.raises(TypeError, match=match):
        AnthropicAdapter(**kwargs)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"api_key": "k"}, "provider_key"),
        ({"gemini_api_key": "k"}, "provider_key"),
        ({"prompt": "hi"}, "instructions"),
        ({"enable_execution_reporting": True}, "features"),
    ],
)
def test_gemini_removed_kwargs(kwargs: dict, match: str) -> None:
    from band.adapters.gemini import GeminiAdapter

    with pytest.raises(TypeError, match=match):
        GeminiAdapter(**kwargs)
