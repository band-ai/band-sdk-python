"""A stopped Copilot turn reopens its room only on the session's own idle."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapter
from band.core.exceptions import InputNotAccepted
from band.core.turn_lifecycle import BACKEND_CANCEL_TIMEOUT_SECONDS
from band.runtime.tools import BandTool
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    HeldRestTools,
    PromptHold,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
)
from tests.adapters.waiting import spin
from tests.framework_conformance.stopprobes import cancel_turn, stop_room, wait_until
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

pytestmark = requires_copilot_sdk


class TestStopConfirmation:
    # The session's compaction-activity subscription outlives every turn.
    ACTIVITY_OBSERVER_ONLY = 1

    @staticmethod
    async def stopped_turn(
        hang: PromptHold | None = None,
    ) -> tuple[CopilotSDKAdapter, FakeCopilotSession, asyncio.Task[None]]:
        """A seeded session whose second turn hangs, with the turn not yet stopped.

        Its aborted idle is withheld, so the stop stays in flight until the
        test delivers one.
        """
        hang = hang or PromptHold(after=1)
        client = FakeCopilotClient(turn_events=[hang])
        adapter = await make_started_adapter(client)
        await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        (session,) = client.sessions
        session.holds_aborted_idle = True
        turn = asyncio.create_task(adapter.on_event(turn_input(HeldRestTools())))
        await wait_until(hang.reached.is_set)
        return adapter, session, turn

    @staticmethod
    def blocked(adapter: CopilotSDKAdapter) -> bool:
        return adapter.backend_turns.unconfirmed_handle(ROOM_ID) is not None

    async def test_only_the_sessions_own_idle_confirms_the_stop(self):
        adapter, session, turn = await self.stopped_turn()
        await cancel_turn(turn)
        await wait_until(lambda: "abort" in session.journal)

        assert self.blocked(adapter)
        session.deliver_subagent_idle()
        await spin()
        assert self.blocked(adapter)

        session.deliver_aborted_idle()
        assert not self.blocked(adapter)
        await stop_room(adapter, ROOM_ID)
        assert len(session.handlers) == self.ACTIVITY_OBSERVER_ONLY

    async def test_the_next_message_prompts_only_after_the_confirmation(self):
        adapter, session, turn = await self.stopped_turn()
        await cancel_turn(turn)
        await wait_until(lambda: "abort" in session.journal)
        session.turn_events = []

        with pytest.raises(InputNotAccepted) as pending:
            await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert pending.value.ready is not None
        assert len(session.prompts) == 2

        session.deliver_subagent_idle()
        await spin()
        assert self.blocked(adapter)
        assert len(session.prompts) == 2

        session.deliver_aborted_idle()
        await pending.value.ready
        await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert len(session.prompts) == 3

    @pytest.mark.looptime
    async def test_an_idle_after_the_stop_was_abandoned_still_confirms_it(self):
        adapter, session, turn = await self.stopped_turn()
        await cancel_turn(turn)
        await wait_until(lambda: "abort" in session.journal)

        await asyncio.sleep(BACKEND_CANCEL_TIMEOUT_SECONDS + 1)
        assert not adapter.backend_turns.stopping(ROOM_ID)
        assert self.blocked(adapter)

        session.deliver_aborted_idle()
        assert not self.blocked(adapter)
        assert len(session.handlers) == self.ACTIVITY_OBSERVER_ONLY

    async def test_an_in_flight_tool_callback_holds_the_confirmation(self):
        tools = HeldRestTools()
        tools.hold_tool_calls = True
        callbacks: list[asyncio.Task[object]] = []

        async def prompt_with_a_tool_call(session: FakeCopilotSession) -> None:
            from copilot import (  # noqa: PLC0415 -- copilot extra, absent from some lane venvs
                ToolInvocation,
            )

            # The SDK runs a tool call on its own task, which a cancelled
            # prompt does not cancel.
            callbacks.append(
                asyncio.create_task(
                    session.find_tool(BandTool.SEND_MESSAGE).handler(
                        ToolInvocation(
                            tool_call_id="call-1",
                            tool_name=BandTool.SEND_MESSAGE,
                            arguments={"content": "hi", "mentions": []},
                        )
                    )
                )
            )
            await asyncio.Event().wait()

        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        (session,) = client.sessions
        session.turn_events = [prompt_with_a_tool_call]
        session.holds_aborted_idle = True
        turn = asyncio.create_task(adapter.on_event(turn_input(tools)))
        await wait_until(tools.tool_started.is_set)
        await cancel_turn(turn)
        await wait_until(lambda: "abort" in session.journal)

        session.deliver_aborted_idle()
        await spin()
        blocked_by_the_callback = self.blocked(adapter)
        tools.tool_released.set()
        await asyncio.gather(*callbacks)

        assert blocked_by_the_callback
        assert not self.blocked(adapter)
