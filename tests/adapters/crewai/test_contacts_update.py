from __future__ import annotations

import pytest


class TestContactsUpdate:
    @pytest.mark.asyncio
    async def test_includes_contacts_update_in_message(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg="[Contacts]: @alice is now a contact",
            is_session_bootstrap=True,
            room_id="room-123",
        )

        call_args = mock_crewai_agent.kickoff_async.call_args
        prompt = call_args[0][0]

        assert "@alice is now a contact" in prompt
