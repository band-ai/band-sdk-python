"""Unit tests for AnthropicProvider (mocked SDK client)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic.types import TextBlock

from band.core.contracts.model import (
    ModelMessage,
    ModelMessageRole,
    ModelRequest,
    ModelSamplingOptions,
)
from band.core.exceptions import UnsupportedOptionError
from band.core.run.context import ProviderModelContext
from band.core.types import TurnUsage
from band.providers.anthropic import AnthropicProvider


@pytest.fixture
def provider() -> AnthropicProvider:
    p = AnthropicProvider(model="claude-test", api_key="k", max_output_tokens=256)
    p.client = MagicMock()
    p.client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            stop_reason="end_turn",
        )
    )
    return p


@pytest.mark.asyncio
async def test_complete_applies_instance_max_tokens(
    provider: AnthropicProvider,
) -> None:
    await provider.complete(
        ModelRequest(
            messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
        ),
        context=ProviderModelContext(),
    )
    kwargs = provider.client.messages.create.await_args.kwargs
    assert kwargs["max_tokens"] == 256
    assert kwargs["model"] == "claude-test"


@pytest.mark.asyncio
async def test_request_sampling_overrides_instance(provider: AnthropicProvider) -> None:
    await provider.complete(
        ModelRequest(
            messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
            sampling=ModelSamplingOptions(max_output_tokens=64, temperature=0.2),
        ),
        context=ProviderModelContext(),
    )
    kwargs = provider.client.messages.create.await_args.kwargs
    assert kwargs["max_tokens"] == 64
    assert kwargs["temperature"] == 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (["hello", "world"], "hello world"),
        (
            ["first paragraph\n\n", "second paragraph"],
            "first paragraph\n\nsecond paragraph",
        ),
        (["hello", " world"], "hello world"),
    ],
)
async def test_complete_joins_text_blocks_without_changing_existing_whitespace(
    provider: AnthropicProvider,
    parts: list[str],
    expected: str,
) -> None:
    provider.client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[TextBlock(type="text", text=part) for part in parts],
            usage=None,
            stop_reason="end_turn",
        )
    )

    response = await provider.complete(
        ModelRequest(messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")]),
        context=ProviderModelContext(),
    )

    assert response.text == expected


def test_unknown_provider_option_rejected() -> None:
    with pytest.raises(UnsupportedOptionError):
        AnthropicProvider(model="m", api_key="k", not_a_real_option=1)  # type: ignore[call-arg]


def test_raw_options_cannot_set_api_key() -> None:
    with pytest.raises(UnsupportedOptionError, match="reserved"):
        AnthropicProvider(model="m", api_key="k", raw_options={"api_key": "x"})


@pytest.mark.asyncio
async def test_raw_options_collision_raises(provider: AnthropicProvider) -> None:
    with pytest.raises(TypeError, match="collides"):
        await provider.complete(
            ModelRequest(
                messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
                sampling=ModelSamplingOptions(temperature=0.5),
                raw_options={"temperature": 0.1},
            ),
            context=ProviderModelContext(),
        )


def test_default_max_output_tokens_when_unset() -> None:
    p = AnthropicProvider(model="m", api_key="k")
    assert p.sampling.max_output_tokens == 4096
    assert p.sampling.temperature is None


def test_raw_options_cannot_ask_for_streaming() -> None:
    """The provider always calls non-streaming and reads the whole response.

    Accepting the flag and then overwriting it makes a caller believe they
    turned streaming on when nothing changed.
    """
    with pytest.raises(UnsupportedOptionError, match="reserved"):
        AnthropicProvider(model="m", api_key="k", raw_options={"stream": True})


@pytest.mark.asyncio
async def test_complete_reports_usage_raw(provider: AnthropicProvider) -> None:
    """Cache counts stay their own dimensions, not folded into input.

    Anthropic reports `input_tokens` already excluding cache, so folding here
    would double-count against providers that report a single total.
    """
    provider.client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi")],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=5,
                cache_creation_input_tokens=3,
            ),
            stop_reason="end_turn",
        )
    )

    response = await provider.complete(
        ModelRequest(messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")]),
        context=ProviderModelContext(),
    )

    assert response.usage == TurnUsage(
        input_tokens=100, output_tokens=20, cache_read_tokens=5, cache_write_tokens=3
    )
