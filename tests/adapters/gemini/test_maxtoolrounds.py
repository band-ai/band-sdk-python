"""MaxToolRounds behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from tests.adapters.gemini.kit import _response_with_function_call


class TestMaxToolRounds:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_max_rounds_exceeded(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter(GeminiAdapterConfig(max_tool_rounds=2))
        await adapter.on_started("TestBot", "Test bot")

        # Always return a function call so the loop never terminates naturally
        with (
            patch.object(
                adapter,
                "_call_gemini",
                AsyncMock(
                    return_value=_response_with_function_call(
                        "band_lookup_peers", {"page": "1"}, "call_1"
                    )
                ),
            ),
            pytest.raises(RuntimeError, match="Exceeded max tool rounds"),
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        # Exceeding max tool rounds is a reportable provider failure.
        mock_tools.send_failure.assert_called_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "gemini"
        assert "Exceeded max tool rounds" in failure.message
