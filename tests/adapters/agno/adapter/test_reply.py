from __future__ import annotations

from agno.run.agent import RunOutput

from tests.adapters.agno.helpers import (
    tool_execution,
)


class TestReply:
    """The adapter delivers nothing on its own. Like the other adapters, the
    agent must call ``band_send_message`` to reach the room; plain agent text is
    never auto-sent and the adapter never guesses a recipient."""

    async def test_no_send_when_agent_returns_only_text(
        self, make_started_adapter, sample_platform_message, tools
    ):
        adapter, _ = await make_started_adapter(RunOutput(content="hello"))

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_no_messages_sent()

    async def test_no_send_when_agent_called_band_send_message(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # The tool call itself reaches the room; the adapter adds nothing on top.
        response = RunOutput(
            content="hello", tools=[tool_execution("band_send_message")]
        )
        adapter, _ = await make_started_adapter(response)

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_no_messages_sent()

    async def test_no_send_for_empty_content(
        self, make_started_adapter, sample_platform_message, tools
    ):
        adapter, _ = await make_started_adapter(RunOutput(content="   "))

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tools.assert_no_messages_sent()
