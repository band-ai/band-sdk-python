"""Retries behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types
from google.genai.errors import ServerError

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from tests.adapters.gemini.kit import _response_with_text


class TestRetries:
    @pytest.mark.asyncio
    async def test_retries_transient_server_errors(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(max_retries=1, retry_base_delay_s=0)
        )
        adapter._system_prompt = "system"
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=[
                ServerError(500, {"error": "temporary"}, None),
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
