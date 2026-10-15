from __future__ import annotations

from datetime import UTC, datetime

import pytest

from band.core.types import PlatformMessage


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(
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
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert "room-123" in adapter._message_history

    @pytest.mark.asyncio
    async def test_loads_existing_history(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        existing_history = [
            {"role": "user", "content": "[Bob]: Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ]

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=existing_history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert len(adapter._message_history["room-123"]) >= 3

    @pytest.mark.asyncio
    async def test_calls_kickoff_async(
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
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        mock_crewai_agent.kickoff_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_replays_history_on_followup_turn(
        self, CrewAIAdapter, sample_message, mock_tools, mock_crewai_agent
    ):
        """A non-bootstrap turn must replay accumulated in-session history.

        The SDK hydrates ``/context`` only at bootstrap; every later turn arrives
        with ``history=[]`` and ``is_session_bootstrap=False``, so the adapter owns
        the running conversation. Guards against re-gating the history block behind
        ``is_session_bootstrap`` (which silently breaks in-session recall — the
        agent would only ever see the current message).
        """
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")
        adapter._crewai_agent = mock_crewai_agent

        # Turn 1 (bootstrap): states something the agent must recall later.
        await adapter.on_message(
            msg=sample_message,  # content="Hello, agent!"
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Turn 2 (follow-up, NOT bootstrap): a fresh message, empty SDK history.
        followup = PlatformMessage(
            id="msg-124",
            room_id="room-123",
            content="What did I say earlier?",
            sender_id="user-456",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        await adapter.on_message(
            msg=followup,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # The second kickoff must carry the prior turn as replayed context, not
        # just the current message.
        prompt = mock_crewai_agent.kickoff_async.call_args_list[1][0][0]
        assert "already handled" in prompt
        assert "Hello, agent!" in prompt  # turn-1 content replayed to the model
