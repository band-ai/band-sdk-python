"""Shared ProviderOptionResolver policy across Anthropic and Gemini providers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.contracts.model import (
    ModelMessage,
    ModelMessageRole,
    ModelRequest,
    ModelSamplingOptions,
)
from band.core.exceptions import UnsupportedOptionError
from band.core.run.context import ProviderModelContext
from band.providers.anthropic import AnthropicProvider
from band.providers.gemini import GeminiProvider


def _anthropic_provider(**kwargs: Any) -> AnthropicProvider:
    p = AnthropicProvider(model="claude-test", api_key="k", **kwargs)
    p.client = MagicMock()
    p.client.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )
    )
    return p


def _gemini_provider(**kwargs: Any) -> GeminiProvider:
    p = GeminiProvider(
        model="gemini-test",
        api_key="k",
        max_retries=0,
        **kwargs,
    )
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text="hi", function_call=None)]
                    ),
                    finish_reason="STOP",
                )
            ],
            usage_metadata=SimpleNamespace(
                prompt_token_count=1, candidates_token_count=1
            ),
        )
    )
    p.client = client
    return p


@pytest.fixture(params=["anthropic", "gemini"])
def provider_kind(request: pytest.FixtureRequest) -> str:
    return request.param  # type: ignore[no-any-return]


def _make(kind: str, **kwargs: Any) -> AnthropicProvider | GeminiProvider:
    match kind:
        case "anthropic":
            return _anthropic_provider(**kwargs)
        case "gemini":
            return _gemini_provider(**kwargs)
        case _:
            raise AssertionError(kind)


def test_reserved_key_rejected_at_init(provider_kind: str) -> None:
    with pytest.raises(UnsupportedOptionError, match="reserved"):
        _make(provider_kind, raw_options={"api_key": "x"})


@pytest.mark.asyncio
async def test_explicit_collision_raises(provider_kind: str) -> None:
    provider = _make(provider_kind, temperature=0.2)
    with pytest.raises(TypeError, match="collides"):
        await provider.complete(
            ModelRequest(
                messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
                sampling=ModelSamplingOptions(temperature=0.5),
                raw_options={"temperature": 0.1},
            ),
            context=ProviderModelContext(),
        )


@pytest.mark.asyncio
async def test_escape_hatch_when_named_option_omitted(provider_kind: str) -> None:
    """raw_options may set a knob the constructor left UNSET."""
    provider = _make(provider_kind, raw_options={"top_p": 0.7})
    await provider.complete(
        ModelRequest(
            messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
        ),
        context=ProviderModelContext(),
    )
    match provider_kind:
        case "anthropic":
            kwargs = provider.client.messages.create.await_args.kwargs  # type: ignore[union-attr]
            assert kwargs["top_p"] == 0.7
        case "gemini":
            config = provider.client.aio.models.generate_content.await_args.kwargs[  # type: ignore[union-attr]
                "config"
            ]
            assert config.top_p == 0.7
        case _:
            raise AssertionError(provider_kind)


@pytest.mark.parametrize("knob", ["temperature", "max_output_tokens"])
@pytest.mark.asyncio
async def test_a_request_sampling_value_is_explicit_against_raw_options(
    provider_kind: str, knob: str
) -> None:
    """A per-request sampling value collides even when the instance set nothing.

    The resolver learns such a value only from what the provider reports it
    applied for the request, so a provider that under-reports would let
    raw_options quietly overwrite the caller's own sampling.
    """
    provider = _make(provider_kind)
    sampling = ModelSamplingOptions(**{knob: 0.5 if knob == "temperature" else 64})

    with pytest.raises(TypeError, match="collides"):
        await provider.complete(
            ModelRequest(
                messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
                sampling=sampling,
                raw_options={knob: 0.9 if knob == "temperature" else 128},
            ),
            context=ProviderModelContext(),
        )


@pytest.mark.asyncio
async def test_anthropic_alias_in_present_blocks_raw_canonical() -> None:
    """Anthropic's required wire default stays provider-managed."""
    provider = _anthropic_provider()
    with pytest.raises(TypeError, match="collides"):
        await provider.complete(
            ModelRequest(
                messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
                raw_options={"max_output_tokens": 99},
            ),
            context=ProviderModelContext(),
        )


@pytest.mark.asyncio
async def test_gemini_omitted_max_output_tokens_allows_raw_override() -> None:
    provider = _gemini_provider()
    await provider.complete(
        ModelRequest(
            messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
            raw_options={"max_output_tokens": 99},
        ),
        context=ProviderModelContext(),
    )

    config = provider.client.aio.models.generate_content.await_args.kwargs["config"]
    assert config.max_output_tokens == 99


@pytest.mark.asyncio
async def test_request_raw_options_override_instance_raw_options(
    provider_kind: str,
) -> None:
    provider = _make(provider_kind, raw_options={"top_p": 0.2})
    await provider.complete(
        ModelRequest(
            messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
            raw_options={"top_p": 0.8},
        ),
        context=ProviderModelContext(),
    )

    match provider_kind:
        case "anthropic":
            kwargs = provider.client.messages.create.await_args.kwargs  # type: ignore[union-attr]
            assert kwargs["top_p"] == 0.8
        case "gemini":
            config = provider.client.aio.models.generate_content.await_args.kwargs[  # type: ignore[union-attr]
                "config"
            ]
            assert config.top_p == 0.8
        case _:
            raise AssertionError(provider_kind)


@pytest.mark.asyncio
async def test_unset_allows_raw_but_none_blocks_it(provider_kind: str) -> None:
    request = ModelRequest(
        messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
        raw_options={"temperature": 0.4},
    )
    await _make(provider_kind).complete(request, context=ProviderModelContext())

    with pytest.raises(TypeError, match="collides"):
        await _make(provider_kind, temperature=None).complete(
            request,
            context=ProviderModelContext(),
        )


@pytest.mark.parametrize(
    ("provider_kind", "reserved_key"),
    [("anthropic", "anthropic_api_key"), ("gemini", "google_api_key")],
)
def test_provider_specific_reserved_key_rejected(
    provider_kind: str, reserved_key: str
) -> None:
    with pytest.raises(UnsupportedOptionError, match="reserved"):
        _make(provider_kind, raw_options={reserved_key: "secret"})
