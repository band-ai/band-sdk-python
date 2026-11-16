"""A stopped Copilot turn reopens its room only on the session's own idle."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapter
from band.core.exceptions import (
    CleanupIncompleteError,
    InputNotAccepted,
)
from band.runtime.tools import BandTool
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    HeldRestTools,
    PromptHold,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)
from tests.adapters.waiting import spin
from tests.framework_conformance.stopprobes import cancel_turn, stop_room, wait_until
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

pytestmark = requires_copilot_sdk


class TestStopConfirmation:
    # The session's compaction-activity subscription outlives every turn.
    ACTIVITY_OBSERVER_ONLY = 1

    @pytest.mark.looptime
    async def test_failed_prompt_cleanup_stays_owned_after_the_stop_budget(
        self,
    ) -> None:
        client = FakeCopilotClient(prompt_errors=[RuntimeError("prompt rejected")])
        client.hold_delete = True
        client.resist_delete_cancel = True
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        turn = asyncio.create_task(run_message(adapter, tools))
        try:
            await client.deleting.wait()
            with pytest.raises(RuntimeError, match="prompt rejected"):
                await turn
            with pytest.raises(InputNotAccepted):
                await run_message(adapter, tools)
            assert len(client.sessions) == 1
            assert client.deleted == []
            assert not adapter.backend_turns.unavailable("room-1")
        finally:
            client.delete_released.set()
            await wait_until(
                lambda: adapter.backend_turns.unconfirmed_handle("room-1") is None
            )
        client.hold_delete = False
        await run_message(adapter, tools)
        assert len(client.sessions) == 2
        assert len(client.sessions[-1].prompts) == 1
        await adapter.cleanup_all()

    @pytest.mark.looptime
    async def test_detaching_a_failed_session_does_not_prove_its_worker_stopped(
        self,
    ) -> None:
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)
        (session,) = client.sessions
        session.send_error = RuntimeError("prompt failed before idle")
        session.holds_aborted_idle = True
        with pytest.raises(RuntimeError, match="prompt failed before idle"):
            await run_message(adapter, tools)
        with pytest.raises(InputNotAccepted):
            await run_message(adapter, tools)
        assert not adapter.backend_turns.unavailable("room-1")

        await session.disconnect()
        with pytest.raises(CleanupIncompleteError):
            await adapter.on_cleanup("room-1")
        assert session.disconnected
        with pytest.raises(InputNotAccepted):
            await run_message(adapter, tools)
        assert len(client.sessions) == 1

        session.deliver_aborted_idle()
        await wait_until(
            lambda: adapter.backend_turns.unconfirmed_handle("room-1") is None
        )
        await run_message(adapter, tools)
        assert len(client.sessions) == 2
        assert len(client.sessions[-1].prompts) == 1
        await adapter.cleanup_all()

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

    async def test_the_next_message_prompts_only_after_the_sessions_own_idle(self):
        adapter, session, turn = await self.stopped_turn()
        await cancel_turn(turn)
        await wait_until(lambda: "abort" in session.journal)
        session.turn_events = []

        with pytest.raises(InputNotAccepted) as pending:
            await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert pending.value.ready is not None
        assert len(session.prompts) == 2

        # A subagent idle is not this stop's confirmation evidence.
        session.deliver_subagent_idle()
        await spin()
        assert self.blocked(adapter)
        assert len(session.prompts) == 2

        session.deliver_aborted_idle()
        await pending.value.ready
        await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert len(session.prompts) == 3
        await stop_room(adapter, ROOM_ID)
        assert len(session.handlers) == self.ACTIVITY_OBSERVER_ONLY

    @pytest.mark.looptime
    async def test_an_idle_after_the_stop_was_abandoned_still_admits_the_next_turn(
        self,
    ):
        adapter, session, turn = await self.stopped_turn()
        await cancel_turn(turn)
        await wait_until(lambda: "abort" in session.journal)
        session.turn_events = []

        await wait_until(lambda: not adapter.backend_turns.stopping(ROOM_ID))
        with pytest.raises(InputNotAccepted) as pending:
            await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert len(session.prompts) == 2

        session.deliver_aborted_idle()
        await pending.value.ready
        await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert len(session.prompts) == 3

    async def test_an_in_flight_tool_callback_holds_admission_until_it_returns(self):
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
        session.turn_events = []

        session.deliver_aborted_idle()
        await spin()
        with pytest.raises(InputNotAccepted) as pending:
            await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert len(session.prompts) == 2

        tools.tool_released.set()
        await asyncio.gather(*callbacks)
        await pending.value.ready
        await adapter.on_event(turn_input(ToolSchemaFakeTools()))
        assert len(session.prompts) == 3
