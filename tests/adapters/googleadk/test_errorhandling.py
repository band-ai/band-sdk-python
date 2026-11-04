"""ErrorHandling behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
)
from tests.adapters.googleadk.bridge import GoogleADKAdapter


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_reports_error_on_runner_failure(self, sample_message, mock_tools):
        """Should report error when ADK runner fails."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            async def failing_run(**kwargs):
                raise RuntimeError("Runner Error")
                yield

            mock_runner.run_async = failing_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            with pytest.raises(Exception, match="Runner Error"):
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
            assert failure.provider == "google_adk"
            assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
            # Runner should be closed even on error (via finally)
            mock_runner.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_reports_error_when_runner_construction_itself_fails(
        self, sample_message, mock_tools
    ):
        """A runner-construction failure must be reported, not escape uncaught."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with (
            patch.object(
                adapter, "_create_runner", side_effect=RuntimeError("bad tool schema")
            ),
            pytest.raises(RuntimeError, match="bad tool schema"),
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
        assert failure.provider == "google_adk"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
