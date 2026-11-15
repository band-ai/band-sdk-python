from __future__ import annotations

import pytest

from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
)
from band.testing import failure_reports
from tests.adapters.crewai.kit import (
    REPLY,
    scripted_crew,
    started,
)
from tests.framework_conformance.turnprobes import turn_input, turn_tools


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_reports_error_on_kickoff_failure(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        mock_crewai_agent.kickoff_async.side_effect = Exception("Agent Error")

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        with pytest.raises(Exception, match="Agent Error"):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.send_failure.assert_awaited_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "crewai"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError("downstream failure after reply"),
            ValueError("a different, genuine LLM problem"),
        ],
        ids=["non-value-error", "unrelated-value-error"],
    )
    async def test_genuine_error_after_reply_still_reports_and_raises(
        self, CrewAIAdapter, error
    ):
        """Only CrewAI's empty-completion ValueError ends a turn normally. Any
        other exception -- even one raised after band_send_message replied --
        is a provider failure that is reported and propagates."""
        adapter = await started(CrewAIAdapter)
        tools = turn_tools()
        adapter._crewai_agent = scripted_crew(adapter, REPLY, ending=error)

        with pytest.raises(type(error), match=str(error)):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [("crewai", GENERIC_PROVIDER_FAILURE_MESSAGE)]

    @pytest.mark.asyncio
    async def test_raises_error_when_agent_not_initialized(
        self, CrewAIAdapter, sample_message, mock_tools
    ):
        """on_message raises RuntimeError if on_started was not called."""
        adapter = CrewAIAdapter()
        # Don't call on_started - agent remains uninitialized

        with pytest.raises(RuntimeError, match="CrewAI agent not initialized"):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.send_failure.assert_awaited_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "crewai"
        assert "not initialized" in failure.message
