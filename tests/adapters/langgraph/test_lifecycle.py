from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from band.adapters.langgraph import LangGraphAdapter
from band.core.types import PlatformMessage

from .helpers import make_capture_graph


class TestInitialization:
    """Tests for adapter initialization."""

    def test_simple_pattern_with_llm(self, mock_llm, mock_checkpointer):
        """Should accept simple pattern with llm and checkpointer."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        assert adapter.graph_factory is not None
        assert adapter._static_graph is None

    def test_simple_pattern_with_additional_tools(self, mock_llm, mock_checkpointer):
        """Should integrate additional_tools in simple pattern."""
        mock_tool = MagicMock()
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            additional_tools=[mock_tool],
        )

        assert adapter.graph_factory is not None
        # additional_tools cleared after baking into factory
        assert adapter.additional_tools == []

    def test_simple_pattern_creates_default_checkpointer(self, mock_llm):
        """The simple path should not silently become stateless."""
        adapter = LangGraphAdapter(llm=mock_llm)

        assert adapter._simple_checkpointer is not None

    def test_advanced_pattern_with_graph_factory(self):
        """Should accept graph_factory for advanced pattern."""
        mock_factory = MagicMock()
        adapter = LangGraphAdapter(graph_factory=mock_factory)

        assert adapter.graph_factory is mock_factory

    def test_advanced_pattern_with_static_graph(self):
        """Should accept static graph for advanced pattern."""
        mock_graph = MagicMock()
        adapter = LangGraphAdapter(graph=mock_graph)

        assert adapter._static_graph is mock_graph

    def test_raises_without_llm_or_graph(self):
        """Should raise if neither llm nor graph_factory/graph provided."""
        with pytest.raises(ValueError, match="Must provide either llm"):
            LangGraphAdapter()


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_renders_system_prompt(self, mock_llm, mock_checkpointer):
        """Should render system prompt from agent metadata."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_includes_custom_section(self, mock_llm, mock_checkpointer):
        """Should include custom_section in system prompt."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            custom_section="Always be concise.",
        )

        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert "Always be concise." in adapter._system_prompt


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleanup_with_graph_factory(self, mock_llm, mock_checkpointer):
        """Should handle cleanup when using graph_factory."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        # Should not raise
        await adapter.on_cleanup("room-123")

    @pytest.mark.asyncio
    async def test_cleanup_without_graph_factory(self):
        """Should handle cleanup when using static graph."""
        mock_graph = MagicMock()
        adapter = LangGraphAdapter(graph=mock_graph)

        # Should not raise
        await adapter.on_cleanup("room-123")

    @pytest.mark.asyncio
    async def test_warns_on_large_bootstrapped_rooms(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Should log a warning when _bootstrapped_rooms reaches threshold."""
        from band.adapters.langgraph import _BOOTSTRAP_TRACKING_WARN_THRESHOLD

        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        adapter._bootstrapped_rooms = OrderedDict(
            (f"room-{i}", None) for i in range(_BOOTSTRAP_TRACKING_WARN_THRESHOLD)
        )

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        with (
            patch(
                "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
            ) as mock_convert,
            patch("band.adapters.langgraph.logger") as mock_logger,
        ):
            mock_convert.return_value = []

            # Bootstrap a new room after the tracking cache is full
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-new",
            )

            mock_logger.warning.assert_any_call(
                "Bootstrap tracking reached %d rooms; evicting oldest room %s",
                _BOOTSTRAP_TRACKING_WARN_THRESHOLD,
                "room-0",
            )

            assert (
                len(adapter._bootstrapped_rooms) == _BOOTSTRAP_TRACKING_WARN_THRESHOLD
            )
            assert "room-0" not in adapter._bootstrapped_rooms
            assert "room-new" in adapter._bootstrapped_rooms

    @pytest.mark.asyncio
    async def test_cleanup_resets_bootstrap_tracking(self, mock_llm, mock_checkpointer):
        """Should clear bootstrap tracking so room can be re-bootstrapped."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        adapter._bootstrapped_rooms["room-123"] = None
        await adapter.on_cleanup("room-123")
        assert "room-123" not in adapter._bootstrapped_rooms

    @pytest.mark.asyncio
    async def test_cleanup_does_not_delete_persistent_checkpointer_state(
        self, mock_llm, mock_checkpointer
    ):
        """Runtime cleanup must not erase user-owned LangGraph persistence."""
        mock_checkpointer.delete_thread = MagicMock()
        mock_checkpointer.adelete_thread = AsyncMock()
        mock_checkpointer.delete_for_runs = MagicMock()
        mock_checkpointer.adelete_for_runs = AsyncMock()
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        adapter._bootstrapped_rooms["room-123"] = None
        adapter._room_checkpointers["room-123"] = mock_checkpointer

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._bootstrapped_rooms
        assert "room-123" not in adapter._room_checkpointers
        mock_checkpointer.delete_thread.assert_not_called()
        mock_checkpointer.adelete_thread.assert_not_awaited()
        mock_checkpointer.delete_for_runs.assert_not_called()
        mock_checkpointer.adelete_for_runs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_restart_with_existing_checkpointer_state_does_not_rehydrate_twice(
        self, mock_tools
    ):
        """Persistent checkpointer state should suppress duplicate bootstrap history."""
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, MessagesState, StateGraph

        checkpointer = InMemorySaver()
        seen_contents: list[list[str]] = []
        seen_system_counts: list[int] = []

        def capture_messages(state: MessagesState) -> dict[str, list[Any]]:
            messages = state["messages"]
            seen_contents.append([getattr(m, "content", "") for m in messages])
            seen_system_counts.append(
                sum(isinstance(m, SystemMessage) for m in messages)
            )
            return {"messages": []}

        builder = StateGraph(MessagesState)
        builder.add_node("capture", capture_messages)
        builder.add_edge(START, "capture")
        builder.add_edge("capture", END)
        graph = builder.compile(checkpointer=checkpointer)

        first_adapter = LangGraphAdapter(graph=graph, inject_system_prompt=True)
        await first_adapter.on_started("TestBot", "Test bot")
        await first_adapter.on_message(
            msg=PlatformMessage(
                id="msg-first",
                room_id="room-123",
                content="first live message",
                sender_id="user-456",
                sender_type="User",
                sender_name="Alice",
                message_type="text",
                metadata={},
                created_at=datetime.now(timezone.utc),
            ),
            tools=mock_tools,
            history=[HumanMessage(content="hydrated prior turn")],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        restarted_adapter = LangGraphAdapter(graph=graph, inject_system_prompt=True)
        await restarted_adapter.on_started("TestBot", "Test bot")
        await restarted_adapter.on_message(
            msg=PlatformMessage(
                id="msg-second",
                room_id="room-123",
                content="second live message",
                sender_id="user-456",
                sender_type="User",
                sender_name="Alice",
                message_type="text",
                metadata={},
                created_at=datetime.now(timezone.utc),
            ),
            tools=mock_tools,
            history=[HumanMessage(content="hydrated prior turn")],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert seen_contents[0].count("hydrated prior turn") == 1
        assert seen_contents[1].count("hydrated prior turn") == 1
        assert seen_system_counts == [1, 1]

    @pytest.mark.asyncio
    async def test_empty_checkpointer_state_still_allows_bootstrap_hydration(self):
        from langgraph.checkpoint.memory import InMemorySaver

        adapter = LangGraphAdapter(graph=MagicMock(), inject_system_prompt=True)

        assert (
            await adapter._checkpointer_has_messages(InMemorySaver(), "room-123")
            is False
        )


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_reports_error_on_graph_failure(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Should report error when graph execution fails."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        async def failing_stream(*args, **kwargs):
            raise Exception("Graph error!")
            yield  # Make it async generator

        mock_graph = MagicMock()
        mock_graph.astream_events = failing_stream
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        with patch(
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            with pytest.raises(Exception, match="Graph error!"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            # Should have tried to report an error event, AND that event
            # must NOT include the raw exception text (it can carry DB
            # strings, paths, tokens, etc.). The full traceback only goes
            # to the agent log via logger.exception.
            mock_tools.send_event.assert_awaited()
            call_kwargs = mock_tools.send_event.call_args.kwargs
            assert call_kwargs["message_type"] == "error"
            assert "Graph error!" not in call_kwargs["content"]
