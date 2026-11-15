"""Config behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from tests.adapters.gemini.kit import _response_with_text


class TestConfig:
    @pytest.mark.parametrize(
        ("setting", "value"),
        [
            ("max_output_tokens", 0),
            ("max_tool_rounds", 0),
            ("max_retries", -1),
            ("retry_base_delay_s", -0.5),
            ("max_history_messages", 0),
        ],
    )
    def test_rejects_out_of_range_settings(self, setting: str, value: float) -> None:
        with pytest.raises(ValueError, match=setting):
            GeminiAdapterConfig.model_validate({setting: value})

    def test_provider_key_authenticates_the_client(self) -> None:
        adapter = GeminiAdapter(GeminiAdapterConfig(provider_key="AIza-test-key"))

        with patch("band.adapters.gemini.genai.Client") as client_cls:
            adapter._ensure_client()

        client_cls.assert_called_once_with(api_key="AIza-test-key")

    @pytest.mark.asyncio
    async def test_requests_use_configured_model_and_sampling(self) -> None:
        adapter = GeminiAdapter(
            GeminiAdapterConfig(
                model="gemini-test-model", max_output_tokens=321, temperature=0.2
            )
        )
        generate = AsyncMock(return_value=_response_with_text("ok"))
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = generate

        await adapter._call_gemini(contents=[], tools=[])

        request = generate.call_args.kwargs
        assert request["model"] == "gemini-test-model"
        assert request["config"].max_output_tokens == 321
        assert request["config"].temperature == 0.2
