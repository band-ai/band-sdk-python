"""What ``NooaAdapter`` accepts before it ever runs a turn."""

from __future__ import annotations

import pytest
from nooa import Agent
from nooa.config.execution_config import ExecutionConfig
from pydantic import BaseModel, create_model

from band.adapters.nooa import NooaAdapter
from band.testing import FakeAgentTools
from band.testing.nooa import scripted_llm, scripted_room_agent
from tests.adapters.nooa.kit import find_order, room_agent_with_execution, room_message


class ClassInput(BaseModel):
    """A tool whose derived name, ``class``, is a Python keyword."""


class TestConstruction:
    def test_the_agent_must_be_a_room_agent_class(self) -> None:
        with pytest.raises(TypeError, match="NooaRoomAgent subclass"):
            NooaAdapter(agent=scripted_room_agent(scripted_llm())())  # type: ignore[arg-type]

    def test_a_plain_nooa_agent_class_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="NooaRoomAgent subclass"):
            NooaAdapter(agent=Agent)  # type: ignore[arg-type]

    def test_a_custom_tool_named_like_a_keyword_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"public Python identifiers: \['class'\]"):
            NooaAdapter(
                agent=scripted_room_agent(scripted_llm()),
                additional_tools=[(ClassInput, find_order)],
            )

    def test_a_custom_tool_may_not_replace_the_turn_binding(self) -> None:
        with pytest.raises(ValueError, match=r"reserved by RoomTools: \['bound_to'\]"):
            NooaAdapter(
                agent=scripted_room_agent(scripted_llm()),
                additional_tools=[(create_model("bound_to"), find_order)],
            )

    async def test_a_message_before_start_fails_loudly(self) -> None:
        adapter = NooaAdapter(agent=scripted_room_agent(scripted_llm()))

        with pytest.raises(RuntimeError, match="on_started must run"):
            await adapter.on_message(
                room_message(),
                FakeAgentTools(),
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id=room_message().room_id,
            )

    async def test_the_agents_execution_settings_reach_every_room(self) -> None:
        agent_class = room_agent_with_execution(ExecutionConfig(max_nesting_depth=37))
        adapter = NooaAdapter(agent=agent_class)

        await adapter.on_started("Agent", "A test agent")

        # NOOA keeps class-level execution settings on this attribute only.
        room_agent_class = adapter._room_agent_class
        assert room_agent_class is not None
        assert room_agent_class._execution_config.max_nesting_depth == 37
