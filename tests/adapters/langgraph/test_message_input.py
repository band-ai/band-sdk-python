from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from band.adapters.langgraph import LangGraphAdapter
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage

from .helpers import make_capture_graph


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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
                "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            features=adapter.features,
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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

        assert (
            Capability.MEMORY in mock_convert.call_args.kwargs["features"].capabilities
        )

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
    async def test_real_compiled_graph_reports_platform_tool_validation_errors(
        self, sample_message, mock_tools
    ):
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode

        from band.integrations.langgraph.langchain_tools import agent_tools_to_langchain

        send_message = next(
            tool
            for tool in agent_tools_to_langchain(mock_tools)
            if tool.name == "band_send_message"
        )

        def request_tool(state: MessagesState) -> dict[str, list[AIMessage]]:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "call-real-platform-tool",
                                "name": "band_send_message",
                                "args": {"content": "hello"},
                            }
                        ],
                    )
                ]
            }

        builder = StateGraph(MessagesState)
        builder.add_node("request_tool", request_tool)
        builder.add_node("tools", ToolNode([send_message]))
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

        mock_tools.execute_tool_call.assert_not_called()
        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "error"
        assert (
            call_kwargs["content"]
            == "band_send_message failed: Invalid arguments for band_send_message: mentions: Field required"
        )

    @pytest.mark.asyncio
    async def test_real_compiled_graph_can_opt_into_bootstrap_system_prompt(
        self, sample_message, mock_tools
    ):
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
