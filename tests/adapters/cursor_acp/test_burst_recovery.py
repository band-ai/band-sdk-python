"""Cursor session setup recovery preserves the room's queued deliveries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.client.streaming import DeliveryStatus
from band.runtime.formatters import InputField
from band.runtime.tools.types import BandTool
from tests.adapters.cursor_acp.kit import ScopedCursorRoom
from tests.conftest import make_message_event
from tests.integrations.acp.acp_toolkit.agent import EXIT_RECORD, FakeACPAgent
from tests.runtime.currentrecords import current_records
from tests.runtime.helpers import ALICE, ROOM_ID
from tests.toolkit.timecontrol import controlled_clock


def replying_agent() -> FakeACPAgent:
    return FakeACPAgent().will_call_mcp_tool(
        "reply",
        BandTool.SEND_MESSAGE,
        arguments={"content": "handled", "mentions": [f"@{ALICE['handle']}"]},
    )


def prompted_ids(agent: FakeACPAgent) -> list[str | None]:
    return [
        record[InputField.MESSAGE_ID]
        for prompt in agent.prompt_texts()
        for record in current_records(prompt)
    ]


class TestBurstRecovery:
    @pytest.mark.looptime
    async def test_an_unanswered_setup_recovers_before_prompting_the_burst(
        self, scoped_cursor_room: Callable[..., Awaitable[ScopedCursorRoom]]
    ) -> None:
        agent = replying_agent()
        opening = agent.holds_next_new_session()
        closing = agent.exits_slowly()
        room = await scoped_cursor_room(agent)
        async with (
            controlled_clock() as clock,
            clock.background(room.deliver("first", "first input")) as burst,
        ):
            try:
                await clock.run_io(opening.received.wait())
                await room.ctx.on_event(
                    make_message_event(
                        room_id=ROOM_ID, msg_id="second", content="second input"
                    )
                )
                clock.advance(room.adapter.config.retention.transition_timeout_s)
                await closing.received.wait()
                assert agent.prompts == []
            finally:
                closing.release.set()
            await clock.run_io(burst)

        assert room.platform.accepted_marks == [
            ("first", DeliveryStatus.PROCESSING),
            ("first", DeliveryStatus.PROCESSED),
            ("second", DeliveryStatus.PROCESSING),
            ("second", DeliveryStatus.PROCESSED),
        ]
        assert room.platform.marked(DeliveryStatus.FAILED) == []
        assert room.platform.failures == []
        assert room.platform.room_messages == ["handled", "handled"]
        assert prompted_ids(agent) == ["first", "second"]
        assert agent.lifecycle == [EXIT_RECORD]

    @pytest.mark.looptime
    async def test_repeated_setup_timeouts_fail_once_and_advance_to_the_next_input(
        self, scoped_cursor_room: Callable[..., Awaitable[ScopedCursorRoom]]
    ) -> None:
        agent = replying_agent()
        opening = agent.holds_next_new_session()
        room = await scoped_cursor_room(agent)
        timeout = room.adapter.config.retention.transition_timeout_s
        async with (
            controlled_clock() as clock,
            clock.background(room.deliver("first", "first input")) as burst,
        ):
            await clock.run_io(opening.received.wait())
            retry = agent.holds_next_new_session()
            await room.ctx.on_event(
                make_message_event(
                    room_id=ROOM_ID, msg_id="second", content="second input"
                )
            )
            clock.advance(timeout)
            await clock.run_io(retry.received.wait())
            clock.advance(timeout)
            await clock.run_io(burst)

        assert room.platform.accepted_marks == [
            ("first", DeliveryStatus.PROCESSING),
            ("first", DeliveryStatus.FAILED),
            ("second", DeliveryStatus.PROCESSING),
            ("second", DeliveryStatus.PROCESSED),
        ]
        assert len(room.platform.failures) == 1
        assert room.platform.room_messages == ["handled"]
        assert prompted_ids(agent) == ["second"]
        assert agent.lifecycle == [EXIT_RECORD, EXIT_RECORD]

    @pytest.mark.looptime
    async def test_slow_setup_that_answers_uses_its_original_connection(
        self, scoped_cursor_room: Callable[..., Awaitable[ScopedCursorRoom]]
    ) -> None:
        agent = replying_agent()
        opening = agent.holds_next_new_session()
        room = await scoped_cursor_room(agent)
        async with (
            controlled_clock() as clock,
            clock.background(room.deliver("first")) as turn,
        ):
            await clock.run_io(opening.received.wait())
            assert agent.prompts == []
            opening.release.set()
            await clock.run_io(turn)

        assert room.platform.marked(DeliveryStatus.PROCESSED) == ["first"]
        assert room.platform.failures == []
        assert prompted_ids(agent) == ["first"]
        assert agent.lifecycle == []
