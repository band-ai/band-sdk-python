"""ErrorReporting behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from google.genai.errors import ServerError

from band.adapters.gemini import GeminiAdapter
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
)


class TestErrorReporting:
    @pytest.mark.asyncio
    async def test_reports_generic_failure(self, sample_message, mock_tools):
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with (
            patch.object(
                adapter, "_call_gemini", AsyncMock(side_effect=Exception("boom"))
            ),
            pytest.raises(Exception, match="boom"),
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

        mock_tools.send_failure.assert_called_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "gemini"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert failure.code is None
        assert failure.detail is None

    @pytest.mark.asyncio
    async def test_preserves_server_error_status_and_message(
        self, sample_message, mock_tools
    ):
        """ServerError's status/message are real provider data -- preserve
        them as code/detail rather than falling back to the generic shape."""
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")
        error = ServerError(
            503, {"error": {"status": "UNAVAILABLE", "message": "overloaded"}}, None
        )

        with (
            patch.object(adapter, "_call_gemini", AsyncMock(side_effect=error)),
            pytest.raises(ServerError),
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

        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "gemini"
        assert failure.code == "UNAVAILABLE"
        assert failure.detail == "overloaded"

    @pytest.mark.asyncio
    async def test_non_string_server_error_status_is_stringified(
        self, sample_message, mock_tools
    ):
        """A malformed error body's non-string ``status`` field must not
        crash failure reporting -- band_sdk_core's AgentFailure requires
        code: str | None, but ServerError.status is an unconstrained
        Optional[str] at runtime (parsed straight off the response JSON)."""
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")
        error = ServerError(503, {"status": 503, "message": "backend overloaded"}, None)

        with (
            patch.object(adapter, "_call_gemini", AsyncMock(side_effect=error)),
            pytest.raises(ServerError),
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

        mock_tools.send_failure.assert_called_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "gemini"
        assert failure.code == "503"
        assert failure.detail == "backend overloaded"
