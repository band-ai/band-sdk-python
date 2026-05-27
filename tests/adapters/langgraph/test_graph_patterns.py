from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.adapters.langgraph import LangGraphAdapter
from thenvoi.core.types import PlatformMessage

from .helpers import make_capture_graph


class TestStaticGraph:
    """Tests for static graph pattern."""

    @pytest.mark.asyncio
    async def test_uses_static_graph_when_provided(self, sample_message, mock_tools):
        """Should use static graph instead of factory when provided."""
        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()

        adapter = LangGraphAdapter(graph=mock_graph)
        await adapter.on_started("TestBot", "Test bot")

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Graph should have been called
            assert "messages" in captured_inputs[0]

    @pytest.mark.asyncio
    async def test_static_graph_with_participants_msg(self, sample_message, mock_tools):
        """Static graph metadata updates stay user-role by default.

        Static graphs may already inject their own system prompt. The adapter
        therefore does not add a Band system message unless the caller opts in.
        """
        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()

        adapter = LangGraphAdapter(graph=mock_graph)
        await adapter.on_started("TestBot", "Test bot")

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice joined the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            messages = captured_inputs[0]["messages"]

            system_msgs = [
                m for m in messages if isinstance(m, tuple) and m[0] == "system"
            ]
            assert system_msgs == []

            user_msgs = [m for m in messages if isinstance(m, tuple) and m[0] == "user"]
            assert len(user_msgs) == 2
            assert "[System]: Alice joined" in user_msgs[0][1]


class TestGraphFactoryMultiRoom:
    """Conformance: ``graph_factory`` must receive each room's own tool wrappers.

    The ``graph_factory(band_tools)`` callback is invoked on every
    ``on_message`` with the wrappers bound to that call's
    ``AgentToolsProtocol``. Examples that cache the compiled graph (and
    therefore the wrappers captured at compile time) silently route every
    room's tool calls to whichever room compiled first. This was the bug
    flagged by review on PR #294 / INT-445.
    """

    @pytest.mark.asyncio
    async def test_factory_receives_distinct_tools_per_room(
        self, mock_llm, mock_checkpointer
    ):
        """Adapter must call graph_factory with the per-room tools every message.

        We don't enforce that the user rebuilds the graph — that's an
        example-quality concern. We DO enforce that the adapter passes the
        right tools to the factory each time, so a correctly-written
        factory has access to the current room's wrappers.
        """
        from langchain_core.tools import StructuredTool

        # Two rooms, two distinct AgentToolsProtocol instances. Wrappers
        # dispatch through ``tools.execute_tool_call(name, kwargs)``, so we
        # only need that method to assert which tools handle which call.
        tools_a = MagicMock()
        tools_a.execute_tool_call = AsyncMock(return_value={"status": "a"})
        tools_a.is_hub_room = False

        tools_b = MagicMock()
        tools_b.execute_tool_call = AsyncMock(return_value={"status": "b"})
        tools_b.is_hub_room = False

        # Capture every (band_tools list) the factory receives.
        received_tool_lists: list[list[Any]] = []
        mock_graph, _captured_inputs, _captured_kwargs = make_capture_graph()

        def graph_factory(band_tools: list[Any]) -> Any:
            received_tool_lists.append(list(band_tools))
            return mock_graph

        adapter = LangGraphAdapter(graph_factory=graph_factory)
        await adapter.on_started("TestBot", "Test bot")

        msg_a = PlatformMessage(
            id="msg-a",
            room_id="room-a",
            content="hi",
            sender_id="u",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )
        msg_b = PlatformMessage(
            id="msg-b",
            room_id="room-b",
            content="hi",
            sender_id="u",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        await adapter.on_message(
            msg=msg_a,
            tools=tools_a,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-a",
        )
        await adapter.on_message(
            msg=msg_b,
            tools=tools_b,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-b",
        )

        # Factory was called twice (once per room).
        assert len(received_tool_lists) == 2

        # Both calls produced StructuredTool wrappers.
        assert all(
            isinstance(t, StructuredTool)
            for tools in received_tool_lists
            for t in tools
        )

        # The two lists are distinct objects (the adapter built fresh
        # wrappers for each room, not the same cached list).
        assert received_tool_lists[0] is not received_tool_lists[1]

        # Invoking a wrapper from each call dispatches to that room's
        # AgentToolsProtocol — proving the closures are bound per-room.
        send_a = next(
            t for t in received_tool_lists[0] if t.name == "thenvoi_send_message"
        )
        send_b = next(
            t for t in received_tool_lists[1] if t.name == "thenvoi_send_message"
        )

        mention_id = "00000000-0000-0000-0000-000000000001"
        await send_a.ainvoke({"content": "from a", "mentions": [mention_id]})
        await send_b.ainvoke({"content": "from b", "mentions": [mention_id]})

        # Each call dispatched to its OWN room's AgentTools — never the other.
        tools_a.execute_tool_call.assert_awaited_once_with(
            "thenvoi_send_message", {"content": "from a", "mentions": [mention_id]}
        )
        tools_b.execute_tool_call.assert_awaited_once_with(
            "thenvoi_send_message", {"content": "from b", "mentions": [mention_id]}
        )
