"""Unit tests for GeminiProvider (mocked SDK client)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google.genai.errors import ServerError

from band.core.contracts.model import (
    ModelMessage,
    ModelMessageRole,
    ModelRequest,
    ModelSamplingOptions,
)
from band.core.exceptions import UnsupportedOptionError
from band.core.run.context import ProviderModelContext
from band.core.types import TurnUsage
from band.providers.gemini import GeminiProvider
from tests.modelclients import ScriptedGeminiClient, gemini_reply


@pytest.fixture
def provider() -> GeminiProvider:
    p = GeminiProvider(
        model="gemini-test",
        api_key="k",
        temperature=0.3,
        max_output_tokens=128,
        max_retries=0,
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


@pytest.mark.asyncio
async def test_complete_sets_sampling(provider: GeminiProvider) -> None:
    await provider.complete(
        ModelRequest(
            messages=[ModelMessage(role=ModelMessageRole.USER, content="x")],
        ),
        context=ProviderModelContext(),
    )
    config = provider.client.aio.models.generate_content.await_args.kwargs["config"]
    assert config.max_output_tokens == 128
    assert config.temperature == 0.3


@pytest.mark.asyncio
async def test_request_overrides_temperature(provider: GeminiProvider) -> None:
    await provider.complete(
        ModelRequest(
            messages=[ModelMessage(role=ModelMessageRole.USER, content="x")],
            sampling=ModelSamplingOptions(temperature=0.9),
        ),
        context=ProviderModelContext(),
    )
    config = provider.client.aio.models.generate_content.await_args.kwargs["config"]
    assert config.temperature == 0.9
    assert config.max_output_tokens == 128


def test_unknown_option_rejected() -> None:
    with pytest.raises(UnsupportedOptionError):
        GeminiProvider(model="m", api_key="k", bogus=True)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_aclose_closes_and_releases_client() -> None:
    provider = GeminiProvider(model="m", api_key="k")
    client = MagicMock()
    provider.client = client

    await provider.aclose()

    client.close.assert_called_once_with()
    assert provider.client is None


@pytest.mark.asyncio
async def test_complete_folds_thinking_tokens_into_output(
    provider: GeminiProvider,
) -> None:
    """Gemini counts thoughts disjointly from candidates.

    Its own `total_token_count` is prompt + candidates + thoughts, so leaving
    `thoughts_token_count` out undercounts every thinking-model turn.
    """
    provider.client.aio.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text="hi", function_call=None)]
                    ),
                    finish_reason="STOP",
                )
            ],
            function_calls=[],
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=20,
                thoughts_token_count=7,
                cached_content_token_count=5,
            ),
        )
    )

    response = await provider.complete(
        ModelRequest(messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")]),
        context=ProviderModelContext(),
    )

    assert response.usage == TurnUsage(
        input_tokens=100, output_tokens=27, cache_read_tokens=5, cache_write_tokens=0
    )


class TestTransientFailures:
    """The provider retries the transient errors the SDK surfaces, and gives up
    once the budget is spent — a turn must not die on one flaky connection."""

    @staticmethod
    def _provider(*replies: object) -> GeminiProvider:
        provider = GeminiProvider(
            model="gemini-test", api_key="k", max_retries=1, retry_base_delay_s=0
        )
        provider.client = ScriptedGeminiClient(list(replies))  # type: ignore[assignment]
        return provider

    @staticmethod
    async def _complete(provider: GeminiProvider):
        return await provider.complete(
            ModelRequest(
                messages=[ModelMessage(role=ModelMessageRole.USER, content="x")]
            ),
            context=ProviderModelContext(),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            ServerError(500, {"error": "temporary"}, None),
            httpx.TimeoutException("timeout"),
            httpx.TransportError("connection reset"),
        ],
        ids=["server-error", "timeout", "transport"],
    )
    async def test_retries_then_succeeds(self, error: Exception) -> None:
        provider = self._provider(error, gemini_reply("ok"))

        response = await self._complete(provider)

        assert provider.client.call_count == 2  # type: ignore[union-attr]
        assert response.text == "ok"

    @pytest.mark.asyncio
    async def test_raises_once_the_retry_budget_is_spent(self) -> None:
        provider = self._provider(
            httpx.TimeoutException("timeout"), httpx.TimeoutException("timeout")
        )

        with pytest.raises(httpx.TimeoutException):
            await self._complete(provider)

        assert provider.client.call_count == 2  # type: ignore[union-attr]


class TestRawOptionsCannotOverrideTheRequest:
    """``raw_options`` is an escape hatch for keys the provider does not set."""

    @pytest.mark.parametrize(
        "key", ["system_instruction", "automatic_function_calling"]
    )
    @pytest.mark.asyncio
    async def test_a_key_the_provider_sets_collides(self, key: str) -> None:
        """Silently replacing one of these changes the prompt or breaks the loop."""
        provider = GeminiProvider(model="gemini-test", api_key="k", max_retries=0)
        provider.client = ScriptedGeminiClient([gemini_reply("ok")])

        with pytest.raises(TypeError, match="collides"):
            await provider.complete(
                ModelRequest(
                    messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
                    system="the real system prompt",
                    raw_options={key: "hijacked"},
                ),
                context=ProviderModelContext(),
            )

    @pytest.mark.asyncio
    async def test_tools_collide_only_once_the_request_carries_them(self) -> None:
        """Unset, ``tools`` is a legitimate escape hatch; set, it is a collision."""
        from band.core.tools import ToolSpec

        provider = GeminiProvider(model="gemini-test", api_key="k", max_retries=0)
        provider.client = ScriptedGeminiClient([gemini_reply("ok"), gemini_reply("ok")])
        spec = ToolSpec(name="greet", description="greet", parameters={})

        await provider.complete(
            ModelRequest(
                messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
                raw_options={"tools": []},
            ),
            context=ProviderModelContext(),
        )

        with pytest.raises(TypeError, match="collides"):
            await provider.complete(
                ModelRequest(
                    messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")],
                    tools=[spec],
                    raw_options={"tools": []},
                ),
                context=ProviderModelContext(),
            )


@pytest.mark.asyncio
async def test_synthesised_tool_call_ids_are_unique_across_rounds() -> None:
    """The id correlates a call with its result and its delivery receipt.

    Gemini usually omits one, so the provider makes it up — and a per-response
    index would restart at zero on the second round and collide with the first.
    """
    from google.genai import types

    def round_with_call(name: str) -> types.GenerateContentResponse:
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(name=name, args={})
                            )
                        ],
                    )
                )
            ]
        )

    provider = GeminiProvider(model="gemini-test", api_key="k", max_retries=0)
    provider.client = ScriptedGeminiClient(
        [round_with_call("first"), round_with_call("second")]
    )
    request = ModelRequest(
        messages=[ModelMessage(role=ModelMessageRole.USER, content="hi")]
    )

    first = await provider.complete(request, context=ProviderModelContext())
    second = await provider.complete(request, context=ProviderModelContext())

    assert first.tool_calls[0].id != second.tool_calls[0].id
