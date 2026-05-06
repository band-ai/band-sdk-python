"""Tests for LangGraphAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_started agent_name/description, on_message callable,
cleanup safety) live in tests/framework_conformance/test_adapter_conformance.py.
This file contains LangGraph-specific behavior: graph factory/static graph
patterns, system prompt rendering, stream event handling, and error handling.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from thenvoi.adapters.langgraph import LangGraphAdapter
from thenvoi.core.types import PlatformMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_capture_graph() -> tuple[
    MagicMock, list[dict[str, Any]], list[dict[str, Any]]
]:
    """Create a mock graph that captures inputs and kwargs sent to ``astream_events``."""
    captured_inputs: list[dict[str, Any]] = []
    captured_kwargs: list[dict[str, Any]] = []

    async def capture_astream_events(graph_input: dict, **kwargs: Any):
        captured_inputs.append(dict(graph_input))
        captured_kwargs.append(dict(kwargs))
        return
        yield  # make it an async generator

    mock_graph = MagicMock()
    mock_graph.astream_events = capture_astream_events
    return mock_graph, captured_inputs, captured_kwargs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_message():
    """Create a sample platform message."""
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.add_participant = AsyncMock(return_value={"id": "user-1"})
    tools.remove_participant = AsyncMock(return_value={"status": "removed"})
    tools.lookup_peers = AsyncMock(return_value={"peers": []})
    tools.get_participants = AsyncMock(return_value=[])
    return tools


@pytest.fixture
def mock_llm():
    """Create mock LangChain LLM."""
    return MagicMock()


@pytest.fixture
def mock_checkpointer():
    """Create mock checkpointer."""
    return MagicMock()


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


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_calls_graph_with_messages(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Should call graph with formatted messages."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        # Patch at the module where it's imported
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

            # Verify graph was created with tools
            adapter.graph_factory.assert_called_once()
            assert "messages" in captured_inputs[0]

    @pytest.mark.asyncio
    async def test_forwards_stream_config_and_version(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            recursion_limit=17,
        )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, _captured_inputs, captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

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

        assert captured_kwargs[0]["version"] == "v2"
        assert captured_kwargs[0]["config"] == {
            "configurable": {"thread_id": "room-123"},
            "recursion_limit": 17,
        }

    @pytest.mark.asyncio
    async def test_simple_factory_passes_platform_and_additional_tools(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        additional_tool = MagicMock(name="additional_tool")
        created_graph, _captured_inputs, _captured_kwargs = make_capture_graph()

        with patch("langchain.agents.create_agent") as mock_create:
            mock_create.return_value = created_graph
            adapter = LangGraphAdapter(
                llm=mock_llm,
                checkpointer=mock_checkpointer,
                additional_tools=[additional_tool],
            )
            await adapter.on_started("TestBot", "Test bot")

            with patch(
                "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
            ) as mock_convert:
                platform_tool = MagicMock(name="platform_tool")
                mock_convert.return_value = [platform_tool]

                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

        mock_create.assert_called_once_with(
            model=mock_llm,
            tools=[platform_tool, additional_tool],
            checkpointer=mock_checkpointer,
        )

    @pytest.mark.asyncio
    async def test_feature_capabilities_control_tool_groups(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        from thenvoi.core.types import AdapterFeatures, Capability

        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(
                capabilities=frozenset({Capability.CONTACTS, Capability.MEMORY})
            ),
        )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, _captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

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

        mock_convert.assert_called_once_with(
            mock_tools,
            include_memory_tools=True,
            include_contacts=True,
        )

    @pytest.mark.asyncio
    async def test_enable_memory_tools_shim_enables_memory_capability(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        with pytest.warns(DeprecationWarning):
            adapter = LangGraphAdapter(
                llm=mock_llm,
                checkpointer=mock_checkpointer,
                enable_memory_tools=True,
            )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, _captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

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

        assert mock_convert.call_args.kwargs["include_memory_tools"] is True

    @pytest.mark.asyncio
    async def test_real_compiled_graph_emits_tool_events(
        self, sample_message, mock_tools
    ):
        from langchain_core.tools import tool
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode

        @tool
        async def record_value(value: str) -> str:
            """Record a value and return it."""
            return f"recorded:{value}"

        def request_tool(state: MessagesState) -> dict[str, list[AIMessage]]:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "call-real-langgraph",
                                "name": "record_value",
                                "args": {"value": "ok"},
                            }
                        ],
                    )
                ]
            }

        builder = StateGraph(MessagesState)
        builder.add_node("request_tool", request_tool)
        builder.add_node("tools", ToolNode([record_value]))
        builder.add_edge(START, "request_tool")
        builder.add_edge("request_tool", "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()

        adapter = LangGraphAdapter(graph=graph)
        await adapter.on_started("TestBot", "Test bot")

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        message_types = [
            call.kwargs["message_type"]
            for call in mock_tools.send_event.await_args_list
        ]
        assert "tool_call" in message_types
        assert "tool_result" in message_types

    @pytest.mark.asyncio
    async def test_includes_system_prompt_on_bootstrap(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Should include system prompt on session bootstrap."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

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

            # System prompt should be in messages
            messages = captured_inputs[0].get("messages", [])
            system_msg = [m for m in messages if m[0] == "system"]
            assert len(system_msg) >= 1

    @pytest.mark.asyncio
    async def test_injects_history_on_bootstrap(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Should inject converted history on bootstrap."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        history = [
            HumanMessage(content="Previous question"),
            AIMessage(content="Previous answer"),
        ]

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=history,
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            messages = captured_inputs[0].get("messages", [])
            # Should include history messages (system + 2 history + user)
            assert len(messages) >= 3

    @pytest.mark.asyncio
    async def test_injects_participants_message(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Should inject participants update as user message with [System]: prefix."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

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

            messages = captured_inputs[0].get("messages", [])
            # Participants info should be a user message with [System]: prefix
            user_msgs = [m for m in messages if isinstance(m, tuple) and m[0] == "user"]
            assert len(user_msgs) == 2
            assert "[System]: Alice joined" in user_msgs[0][1]
            assert "Hello, agent!" in user_msgs[1][1]

    @pytest.mark.asyncio
    async def test_no_extra_system_messages_with_history_and_participants(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Regression: participants_msg must not create additional system messages.

        When is_session_bootstrap=True with history AND participants_msg, the old code
        produced [system, user, assistant, system, user] which Anthropic rejects.
        The fix injects metadata as user messages with [System]: prefix so only the
        initial system prompt is a system-role message.
        """
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        history = [
            HumanMessage(content="Previous question"),
            AIMessage(content="Previous answer"),
        ]

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=history,
                participants_msg="Alice joined the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            messages = captured_inputs[0].get("messages", [])

            # Only the system prompt should be a system message
            system_msgs = [
                m for m in messages if isinstance(m, tuple) and m[0] == "system"
            ]
            assert len(system_msgs) == 1  # just the system prompt

            # Participants info should be a user message with prefix
            user_msgs = [m for m in messages if isinstance(m, tuple) and m[0] == "user"]
            assert len(user_msgs) == 2
            assert "[System]: Alice joined" in user_msgs[0][1]
            assert "Hello, agent!" in user_msgs[1][1]

    @pytest.mark.asyncio
    async def test_participants_as_user_message_on_non_bootstrap(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """participants_msg on non-bootstrap should be a user message with [System]: prefix.

        On non-bootstrap turns, no system-role messages are injected at all.
        The checkpointer holds the original system prompt from bootstrap.
        """
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Bob joined the room",
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

            messages = captured_inputs[0].get("messages", [])

            # No system messages on non-bootstrap
            system_msgs = [
                m for m in messages if isinstance(m, tuple) and m[0] == "system"
            ]
            assert len(system_msgs) == 0

            # Participants as user message + original user message
            user_msgs = [m for m in messages if isinstance(m, tuple) and m[0] == "user"]
            assert len(user_msgs) == 2
            assert "[System]: Bob joined" in user_msgs[0][1]
            assert "Hello, agent!" in user_msgs[1][1]

    @pytest.mark.asyncio
    async def test_no_duplicate_system_prompt_on_re_bootstrap(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Regression: re-bootstrap must not inject duplicate system prompt.

        When an agent reconnects, a new ExecutionContext triggers is_session_bootstrap
        again, but the checkpointer already has the system prompt from the first
        bootstrap. Injecting another would create non-consecutive system messages.
        """
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            # First bootstrap - should include system prompt
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            first_messages = captured_inputs[0]["messages"]
            system_msgs = [m for m in first_messages if m[0] == "system"]
            assert len(system_msgs) == 1

            # Second bootstrap (reconnection) - should NOT include system prompt
            # because checkpointer already has it
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            second_messages = captured_inputs[1]["messages"]
            system_msgs = [m for m in second_messages if m[0] == "system"]
            assert len(system_msgs) == 0, (
                f"Re-bootstrap should not inject system prompt, got: {system_msgs}"
            )


class TestStreamEventHandling:
    """Tests for _handle_stream_event() method."""

    @pytest.mark.asyncio
    async def test_handles_on_tool_start(self, mock_tools, mock_llm, mock_checkpointer):
        """Should send tool_call event on on_tool_start."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        event = {
            "event": "on_tool_start",
            "name": "thenvoi_send_message",
            "run_id": "run-123",
            "data": {"input": {"content": "Hello"}},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "tool_call"

    @pytest.mark.asyncio
    async def test_handles_on_tool_end(self, mock_tools, mock_llm, mock_checkpointer):
        """Should send tool_result event on on_tool_end."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        event = {
            "event": "on_tool_end",
            "name": "thenvoi_send_message",
            "run_id": "run-123",
            "data": {"output": "success"},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "tool_result"

    @pytest.mark.asyncio
    async def test_ignores_other_events(self, mock_tools, mock_llm, mock_checkpointer):
        """Should ignore events other than tool_start/end."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        event = {
            "event": "on_chat_model_start",
            "name": "ChatOpenAI",
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_not_awaited()


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
        from thenvoi.adapters.langgraph import _BOOTSTRAP_TRACKING_WARN_THRESHOLD

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
                "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
            ) as mock_convert,
            patch("thenvoi.adapters.langgraph.logger") as mock_logger,
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
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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

            # Should have tried to report error
            mock_tools.send_event.assert_awaited()


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
        """Should inject participants as user message with static graph.

        Static graphs don't inject a system prompt (no graph_factory).
        participants_msg is injected as a user message with [System]: prefix.
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

            # No system messages with static graph
            system_msgs = [
                m for m in messages if isinstance(m, tuple) and m[0] == "system"
            ]
            assert len(system_msgs) == 0

            user_msgs = [m for m in messages if isinstance(m, tuple) and m[0] == "user"]
            assert len(user_msgs) == 2
            assert "[System]: Alice joined" in user_msgs[0][1]
            assert "Hello, agent!" in user_msgs[1][1]
