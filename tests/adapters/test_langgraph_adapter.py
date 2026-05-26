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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from thenvoi.adapters.langgraph import LangGraphAdapter
from thenvoi.core.types import AdapterFeatures, Emit, PlatformMessage


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
            "configurable": {
                "thread_id": "room-123",
            },
            "recursion_limit": 17,
        }

    @pytest.mark.asyncio
    async def test_static_graph_does_not_inject_system_prompt_by_default(
        self, sample_message, mock_tools
    ):
        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter = LangGraphAdapter(graph=mock_graph)
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

        messages = captured_inputs[0]["messages"]
        assert all(not (isinstance(m, tuple) and m[0] == "system") for m in messages)

    @pytest.mark.asyncio
    async def test_graph_factory_does_not_inject_system_prompt_by_default(
        self, sample_message, mock_tools
    ):
        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter = LangGraphAdapter(graph_factory=MagicMock(return_value=mock_graph))
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

        messages = captured_inputs[0]["messages"]
        assert all(not (isinstance(m, tuple) and m[0] == "system") for m in messages)

    @pytest.mark.asyncio
    async def test_advanced_graph_can_opt_into_bootstrap_system_prompt(
        self, sample_message, mock_tools
    ):
        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter = LangGraphAdapter(
            graph_factory=MagicMock(return_value=mock_graph),
            inject_system_prompt=True,
        )
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

        messages = captured_inputs[0]["messages"]
        assert messages[0] == ("system", adapter._system_prompt)
        assert "TestBot" in messages[0][1]

    @pytest.mark.asyncio
    async def test_graph_factory_rebinds_tools_per_room(self, sample_message):
        """Advanced graph factories must not reuse room-bound tool wrappers."""
        tools_room_a = MagicMock(name="tools_room_a")
        tools_room_b = MagicMock(name="tools_room_b")
        platform_tool_a = MagicMock(name="platform_tool_a")
        platform_tool_b = MagicMock(name="platform_tool_b")
        graph_a, _inputs_a, kwargs_a = make_capture_graph()
        graph_b, _inputs_b, kwargs_b = make_capture_graph()
        factory_tools: list[list[Any]] = []

        def graph_factory(band_tools: list[Any]):
            factory_tools.append(list(band_tools))
            return graph_a if len(factory_tools) == 1 else graph_b

        adapter = LangGraphAdapter(graph_factory=graph_factory)
        await adapter.on_started("TestBot", "Test bot")

        room_b_message = PlatformMessage(
            id="msg-456",
            room_id="room-B",
            content="Hello from room B",
            sender_id="user-456",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.side_effect = [[platform_tool_a], [platform_tool_b]]

            await adapter.on_message(
                msg=sample_message,
                tools=tools_room_a,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-A",
            )
            await adapter.on_message(
                msg=room_b_message,
                tools=tools_room_b,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-B",
            )

        assert factory_tools == [[platform_tool_a], [platform_tool_b]]
        assert kwargs_a[0]["config"]["configurable"]["thread_id"] == "room-A"
        assert kwargs_b[0]["config"]["configurable"]["thread_id"] == "room-B"

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
        from thenvoi.core.types import Capability

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

        adapter = LangGraphAdapter(
            graph=graph,
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
        )
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
    async def test_real_compiled_graph_can_opt_into_bootstrap_system_prompt(
        self, sample_message, mock_tools
    ):
        from langchain_core.messages import SystemMessage
        from langgraph.graph import END, START, MessagesState, StateGraph

        seen_prompts: list[str] = []

        def capture_prompt(state: MessagesState) -> dict[str, list]:
            for m in state["messages"]:
                if isinstance(m, SystemMessage):
                    seen_prompts.append(m.content)
            return {"messages": []}

        builder = StateGraph(MessagesState)
        builder.add_node("capture_prompt", capture_prompt)
        builder.add_edge(START, "capture_prompt")
        builder.add_edge("capture_prompt", END)
        graph = builder.compile()

        adapter = LangGraphAdapter(graph=graph, inject_system_prompt=True)
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

        assert len(seen_prompts) == 1
        assert "TestBot" in seen_prompts[0]

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
            assert len(messages) == 4
            assert messages[0] == ("system", adapter._system_prompt)
            assert messages[1] is history[0]
            assert messages[1].content == "Previous question"
            assert messages[2] is history[1]
            assert messages[2].content == "Previous answer"
            assert messages[3] == ("user", "[Alice]: Hello, agent!")

    @pytest.mark.asyncio
    async def test_bootstrap_injects_hydrated_own_reply_once(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
        """Bootstrap should pass prior own replies through to LangGraph once."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )
        await adapter.on_started("TestBot", "Test bot")

        current_message = PlatformMessage(
            id=sample_message.id,
            room_id=sample_message.room_id,
            content="What word did I ask you to remember?",
            sender_id=sample_message.sender_id,
            sender_type=sample_message.sender_type,
            sender_name=sample_message.sender_name,
            message_type=sample_message.message_type,
            metadata=sample_message.metadata,
            created_at=sample_message.created_at,
        )
        history = [
            HumanMessage(content="[Alice]: Remember papaya"),
            AIMessage(content="I will remember papaya"),
        ]

        mock_graph, captured_inputs, _captured_kwargs = make_capture_graph()
        adapter.graph_factory = MagicMock(return_value=mock_graph)

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            await adapter.on_message(
                msg=current_message,
                tools=mock_tools,
                history=history,
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        messages = captured_inputs[0]["messages"]
        assert messages == [
            ("system", adapter._system_prompt),
            history[0],
            history[1],
            ("user", "[Alice]: What word did I ask you to remember?"),
        ]

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
        """Regression: participants_msg must not produce a second system message.

        Anthropic rejects multiple system messages and many providers lose
        prompt-cache savings when extra system messages appear mid-conversation.
        Bootstrap injects exactly one ``("system", ...)`` (the rendered prompt)
        and inlines metadata as ``("user", "[System]: ...")``.
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

            # Exactly one system-role message: the rendered Band prompt.
            system_msgs = [
                m for m in messages if isinstance(m, tuple) and m[0] == "system"
            ]
            assert len(system_msgs) == 1
            assert system_msgs[0][1] == adapter._system_prompt

            # Participants info is a user message with prefix.
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
        """Regression: re-bootstrap must not re-hydrate history.

        When an agent reconnects, a new ExecutionContext triggers
        is_session_bootstrap again, but the checkpointer already has prior
        turns from the first bootstrap. Re-pushing history would duplicate
        graph state.
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

            # First bootstrap with history.
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[HumanMessage(content="hydrated prior turn")],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            first_messages = captured_inputs[0]["messages"]
            assert any(
                getattr(m, "content", None) == "hydrated prior turn"
                for m in first_messages
            )

            # Second bootstrap (reconnection) should not re-hydrate history.
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[HumanMessage(content="hydrated prior turn")],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            second_messages = captured_inputs[1]["messages"]
            assert all(
                getattr(m, "content", None) != "hydrated prior turn"
                for m in second_messages
            )


class TestStreamEventHandling:
    """Tests for _handle_stream_event() method."""

    @pytest.mark.asyncio
    async def test_handles_on_tool_start(self, mock_tools, mock_llm, mock_checkpointer):
        """Should send tool_call event on on_tool_start."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
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
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
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

    @pytest.mark.asyncio
    async def test_ignores_malformed_events(
        self, mock_tools, mock_llm, mock_checkpointer
    ):
        """Malformed stream payloads should not crash event handling."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        await adapter._handle_stream_event(["not", "a", "dict"], "room-123", mock_tools)

        mock_tools.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_emit_when_execution_feature_off(
        self, mock_tools, mock_llm, mock_checkpointer
    ):
        """Execution stream events are gated by Emit.EXECUTION."""
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

        mock_tools.send_event.assert_not_awaited()

    def test_enable_execution_reporting_shim_enables_execution_emit(
        self, mock_llm, mock_checkpointer
    ):
        """Legacy execution-reporting flag maps to Emit.EXECUTION."""
        with pytest.warns(DeprecationWarning):
            adapter = LangGraphAdapter(
                llm=mock_llm,
                checkpointer=mock_checkpointer,
                enable_execution_reporting=True,
            )

        assert Emit.EXECUTION in adapter.features.emit


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

    @pytest.mark.asyncio
    async def test_cleanup_clears_checkpointer_thread(self, sample_message, mock_tools):
        """Cleanup must remove saved graph state before room re-entry.

        Otherwise a room_added -> cleanup -> room_added lifecycle replays the
        hydrated history into the existing checkpointer thread and duplicates
        SystemMessages, which hard-fails ChatAnthropic.
        """
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, MessagesState, StateGraph

        checkpointer = InMemorySaver()
        seen_system_counts: list[int] = []

        def capture_systems(state: MessagesState) -> dict[str, list[Any]]:
            seen_system_counts.append(
                sum(isinstance(m, SystemMessage) for m in state["messages"])
            )
            return {"messages": []}

        builder = StateGraph(MessagesState)
        builder.add_node("capture", capture_systems)
        builder.add_edge(START, "capture")
        builder.add_edge("capture", END)
        graph = builder.compile(checkpointer=checkpointer)

        adapter = LangGraphAdapter(graph=graph, inject_system_prompt=True)
        await adapter.on_started("TestBot", "Test bot")

        for content in ("first lifecycle", "second lifecycle"):
            await adapter.on_message(
                msg=PlatformMessage(
                    id=f"msg-{content}",
                    room_id="room-123",
                    content=content,
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
            await adapter.on_cleanup("room-123")

        assert seen_system_counts == [1, 1]


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

            # Should have tried to report an error event, AND that event
            # must NOT include the raw exception text (it can carry DB
            # strings, paths, tokens, etc.). The full traceback only goes
            # to the agent log via logger.exception.
            mock_tools.send_event.assert_awaited()
            call_kwargs = mock_tools.send_event.call_args.kwargs
            assert call_kwargs["message_type"] == "error"
            assert "Graph error!" not in call_kwargs["content"]


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

        await send_a.ainvoke({"content": "from a", "mentions": ["@x"]})
        await send_b.ainvoke({"content": "from b", "mentions": ["@x"]})

        # Each call dispatched to its OWN room's AgentTools — never the other.
        tools_a.execute_tool_call.assert_awaited_once_with(
            "thenvoi_send_message", {"content": "from a", "mentions": ["@x"]}
        )
        tools_b.execute_tool_call.assert_awaited_once_with(
            "thenvoi_send_message", {"content": "from b", "mentions": ["@x"]}
        )


class TestSystemPromptCrossTurn:
    """The bootstrap-once + checkpointer-carry-forward contract.

    Turn 1 (``is_session_bootstrap=True``): the adapter prepends a single
    ``("system", rendered_prompt)`` to the graph input. The user's graph
    runs and the checkpointer persists that ``SystemMessage`` as part of
    the conversation state.

    Turn 2 (``is_session_bootstrap=False``): the adapter MUST NOT prepend
    the system prompt again. The checkpointer is responsible for carrying
    the original ``SystemMessage`` forward; double-prepending would either
    confuse the model with two conflicting system messages or get rejected
    by APIs that disallow consecutive system roles.

    Previously only verified live (room ``306ac939-…`` in the PR body).
    """

    @pytest.mark.asyncio
    async def test_bootstrap_prepends_system_prompt_then_subsequent_turns_do_not(
        self, sample_message, mock_tools, mock_llm, mock_checkpointer
    ):
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

            # Turn 1: bootstrap. The rendered system prompt must lead.
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-xt",
            )

            turn1 = captured_inputs[0]["messages"]
            turn1_systems = [
                m for m in turn1 if isinstance(m, tuple) and m[0] == "system"
            ]
            assert len(turn1_systems) == 1
            assert "TestBot" in turn1_systems[0][1]

            # Turn 2: same room, NOT a bootstrap. The adapter must rely on
            # the checkpointer for system-prompt carry-forward and NOT
            # prepend a second system message itself.
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-xt",
            )

            turn2 = captured_inputs[1]["messages"]
            turn2_systems = [
                m for m in turn2 if isinstance(m, tuple) and m[0] == "system"
            ]
            assert turn2_systems == []

    @pytest.mark.asyncio
    async def test_real_checkpointer_carries_system_prompt_forward(
        self, sample_message, mock_tools, mock_llm
    ):
        """End-to-end with a real ``InMemorySaver``.

        After turn 1, the checkpointer's stored state must contain the
        ``SystemMessage`` the adapter prepended on bootstrap. Turn 2 then
        runs without the adapter re-prepending — this proves the
        checkpointer (not the adapter) is what keeps the system prompt
        present across turns.
        """
        from langchain_core.messages import SystemMessage
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, MessagesState, StateGraph

        checkpointer = InMemorySaver()
        seen_system_prompts: list[list[str]] = []

        def echo_node(state: MessagesState) -> dict[str, list[Any]]:
            seen_system_prompts.append(
                [m.content for m in state["messages"] if isinstance(m, SystemMessage)]
            )
            # Return a no-op so we don't pollute the saved state with extra
            # AI messages — the test only inspects what the node observed.
            return {"messages": []}

        builder = StateGraph(MessagesState)
        builder.add_node("echo", echo_node)
        builder.add_edge(START, "echo")
        builder.add_edge("echo", END)
        graph = builder.compile(checkpointer=checkpointer)

        adapter = LangGraphAdapter(graph=graph, inject_system_prompt=True)
        await adapter.on_started("TestBot", "Test bot")

        with patch(
            "thenvoi.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
        ) as mock_convert:
            mock_convert.return_value = []

            # Turn 1: bootstrap injects ("system", rendered_prompt).
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-xt-real",
            )

            # Turn 2: same room, NOT a bootstrap. Adapter sends no system
            # message; the checkpointer must replay it.
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-xt-real",
            )

        # The node saw exactly one SystemMessage on turn 1 (from the
        # adapter) and exactly one on turn 2 (carried by the checkpointer
        # — same content). Assert both turns observed it.
        assert len(seen_system_prompts) == 2
        assert len(seen_system_prompts[0]) == 1
        assert "TestBot" in seen_system_prompts[0][0]
        assert len(seen_system_prompts[1]) == 1
        assert seen_system_prompts[1][0] == seen_system_prompts[0][0]
