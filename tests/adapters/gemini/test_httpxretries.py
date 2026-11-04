"""HttpxRetries behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.genai import types

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from tests.adapters.gemini.kit import _response_with_text


class TestHttpxRetries:
    @pytest.mark.asyncio
    async def test_retries_on_timeout_exception(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timeout"),
                _response_with_text("ok"),
            ]
        )

        with patch.object(
            adapter.client.aio.models,  # type: ignore[union-attr]
            "generate_content",
            adapter.client.aio.models.generate_content,
        ) as mocked:
            response = await adapter._call_gemini(
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text="x")])
                ],
                tools=[],
            )

        assert mocked.call_count == 2
        assert response.candidates[0].content.parts[0].text == "ok"

    @pytest.mark.asyncio
    async def test_retries_on_transport_error(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=[
                httpx.TransportError("connection reset"),
                _response_with_text("ok"),
            ]
        )

        with patch.object(
            adapter.client.aio.models,  # type: ignore[union-attr]
            "generate_content",
            adapter.client.aio.models.generate_content,
        ) as mocked:
            response = await adapter._call_gemini(
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text="x")])
                ],
                tools=[],
            )

        assert mocked.call_count == 2
        assert response.candidates[0].content.parts[0].text == "ok"

    @pytest.mark.asyncio
    async def test_raises_after_exhausting_retries(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with (
            patch.object(
                adapter.client.aio.models,  # type: ignore[union-attr]
                "generate_content",
                adapter.client.aio.models.generate_content,
            ),
            pytest.raises(httpx.TimeoutException),
        ):
            await adapter._call_gemini(
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text="x")])
                ],
                tools=[],
            )
