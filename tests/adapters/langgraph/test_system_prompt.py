from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import SystemMessage

from band.adapters.langgraph import LangGraphAdapter

from .helpers import make_capture_graph


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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
            "band.integrations.langgraph.langchain_tools.agent_tools_to_langchain"
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
