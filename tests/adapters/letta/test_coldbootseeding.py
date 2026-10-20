from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    RoomContext,
)
from band.converters.letta import LettaSessionState
from band.core.protocols import (
    TurnResultAlreadyReported,
)
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.lettakit import (
    make_assistant_message,
    make_letta_response,
    make_mock_agent,
    make_mock_tool_page,
    make_platform_message,
)


class TestColdBootSeeding:
    @pytest.fixture
    def adapter_with_client(self) -> tuple[LettaAdapter, AsyncMock]:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test prompt"
        adapter._mcp.server_id = "mcp-server-1"
        adapter._mcp.tool_ids = []
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_new_agent_in_room_with_history_is_seeded(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client
        mock_client.agents.create.return_value = make_mock_agent("fresh-agent")
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hi again!")
        )

        history = LettaSessionState(
            replay_messages=["[Alice]: The secret word is kumquat.", "[Bot]: Noted!"]
        )
        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(content="what was the secret word?"),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        content = mock_client.agents.messages.create.call_args.kwargs["messages"][0][
            "content"
        ]
        assert "joining an ongoing conversation" in content
        assert "[Alice]: The secret word is kumquat." in content
        assert "what was the secret word?" in content
        # Seed is delivered exactly once
        assert adapter._rooms["room-1"].pending_seed == []

    @pytest.mark.asyncio
    async def test_reported_turn_consumes_delivered_seed(self) -> None:
        """A completed provider call consumes its seed before response handling."""
        adapter = LettaAdapter(config=LettaAdapterConfig(auto_relay=False))
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test prompt"
        adapter._mcp.server_id = "mcp-server-1"
        adapter._mcp.tool_ids = []
        room_ctx = RoomContext(
            agent_id="agent-1", pending_seed=["[Alice]: Earlier context"]
        )
        adapter._rooms["room-1"] = room_ctx
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("The response was not sent through the tool.")
        )

        await adapter.on_message(
            make_platform_message(),
            FakeAgentTools(),
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert room_ctx.pending_seed == []
        assert room_ctx.last_interaction is not None

    @pytest.mark.asyncio
    async def test_failed_first_turn_preserves_pending_seed(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """Cold-boot history must survive a failed first turn — clearing the
        seed at compose time would drop replay context forever."""
        adapter, mock_client = adapter_with_client
        mock_client.agents.create.return_value = make_mock_agent("fresh-agent")
        mock_client.agents.messages.create.side_effect = TimeoutError("slow letta")

        history = LettaSessionState(
            replay_messages=["[Alice]: The secret word is kumquat."]
        )
        tools = FakeAgentTools()
        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_message(
                make_platform_message(content="what was the secret word?"),
                tools,
                history,
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert adapter._rooms["room-1"].pending_seed == [
            "[Alice]: The secret word is kumquat."
        ]

    @pytest.mark.asyncio
    async def test_per_room_does_not_resume_config_agent_id(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """config.agent_id is not a per-room resume target — each room without
        persisted history must get its own Letta agent."""
        adapter, mock_client = adapter_with_client
        adapter.config = adapter.config.model_copy(
            update={"agent_id": "shared-bootstrap-id"}
        )
        mock_client.agents.create.return_value = make_mock_agent("room-agent")
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hello!")
        )

        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            FakeAgentTools(),
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        mock_client.agents.retrieve.assert_not_called()
        mock_client.agents.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_second_message_is_not_reseeded(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client
        mock_client.agents.create.return_value = make_mock_agent("fresh-agent")
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hello!")
        )

        history = LettaSessionState(replay_messages=["[Alice]: earlier message"])
        tools = FakeAgentTools()
        for _ in range(2):
            await adapter.on_message(
                make_platform_message(),
                tools,
                history,
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        second_content = mock_client.agents.messages.create.call_args.kwargs[
            "messages"
        ][0]["content"]
        assert "[Alice]: earlier message" not in second_content

    @pytest.mark.asyncio
    async def test_resumed_agent_is_not_seeded(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """Resume-by-id stays the fast path — the live agent already carries
        its own context, so replaying history would duplicate it."""
        adapter, mock_client = adapter_with_client
        mock_client.agents.retrieve.return_value = make_mock_agent("live-agent")
        mock_client.agents.tools.list.return_value = make_mock_tool_page()
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Resumed!")
        )

        history = LettaSessionState(
            agent_id="live-agent",
            replay_messages=["[Alice]: earlier message"],
        )
        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        content = mock_client.agents.messages.create.call_args.kwargs["messages"][0][
            "content"
        ]
        assert "[Alice]: earlier message" not in content
        mock_client.agents.create.assert_not_called()
