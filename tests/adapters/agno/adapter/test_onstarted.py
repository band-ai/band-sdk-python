from __future__ import annotations

from agno.run.agent import RunOutput

from band.adapters.agno import (
    AgnoAdapter,
)
from tests.adapters.agno.adapter.kit import _msg


class TestOnStarted:
    async def test_runs_against_the_given_agent(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent=agent)

        await adapter.on_started("TestBot", "desc")

        # The adapter uses the caller's instance directly, no copy.
        assert adapter.agent is agent

    async def test_syncs_converter_identity(self, make_started_adapter):
        adapter, _ = await make_started_adapter()

        assert adapter.history_converter._agent_name == "TestBot"

    async def test_runs_the_given_agent_on_a_message(self, make_agno_agent, tools):
        # End-to-end: the given agent must be the one actually run on a message.
        # The adapter delivers nothing on its own; plain agent text is not sent
        # (only a ``band_send_message`` tool call reaches the room).
        agent = make_agno_agent(response=RunOutput(content="hi there"))
        adapter = AgnoAdapter(agent=agent, emit=())
        await adapter.on_started("TestBot", "desc")

        await adapter.on_message(
            _msg("room-1", "hello"),
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        agent.arun.assert_called_once()
        tools.assert_no_messages_sent()
