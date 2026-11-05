"""A cancelled start cannot strand unknown native work on a reused process."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.codex import CodexRequestMethod
from band.platform.link import BandLink
from band.runtime.delivery import TurnOutcome
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
    CountingCloseClient,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.waiting import until
from tests.runtime.helpers import (
    AGENT_ID,
    PostingPlatform,
    rest_client_over,
    run_through,
)
from tests.runtime.helpers import (
    ROOM_ID as RUNTIME_ROOM_ID,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestCancelledStartRecovery:
    async def test_failed_stop_close_retains_input_without_spending_its_failure_budget(
        self,
    ) -> None:
        client = CountingCloseClient(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed("turn-1")]
        )
        room = CodexRoom(make_codex_adapter(client), client)
        await room.adapter.on_started("Codex", "test agent")
        peer = PostingPlatform()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(room.adapter, link)
            try:
                peer.add_message("seed")
                assert (
                    await attempt_delivery(ctx, peer.event("seed"))
                    is TurnOutcome.ADVANCED
                )
                working = asyncio.create_task(
                    send_bootstrap(room.adapter, room_id=RUNTIME_ROOM_ID)
                )
                await until(
                    lambda: len(client.params_of(CodexRequestMethod.TURN_START)) == 2,
                    failure="second turn did not start",
                )
                client.refuse_close = True
                working.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await working
                await room.adapter.backend_turns.drain()
                peer.add_message("pending")
                event = peer.event("pending")
                assert await attempt_delivery(ctx, event) is TurnOutcome.NOT_ACCEPTED
                assert await attempt_delivery(ctx, event) is TurnOutcome.NOT_ACCEPTED
                assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
                assert peer.marked("failed") == []
                client.refuse_close = False
                client.script(
                    tool_call_request(2, BandTool.NO_REPLY), turn_completed("turn-3")
                )
                assert await attempt_delivery(ctx, event) is TurnOutcome.ADVANCED
                assert peer.marked("processed") == ["seed", "pending"]
                assert peer.marked("failed") == []
                assert client.starts == 2
            finally:
                client.refuse_close = False
                await ctx.stop()
                await room.adapter.on_cleanup(RUNTIME_ROOM_ID)

    async def test_seeded_process_is_retired_before_a_cancelled_start_answers(
        self,
    ) -> None:
        client = CountingCloseClient(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed("turn-1")]
        )
        room = CodexRoom(make_codex_adapter(client), client)
        await room.adapter.on_started("Codex", "test agent")
        try:
            await room.send("seed")
            client.turn_start_released.clear()
            turn = asyncio.create_task(room.send("start unknown work"))
            await until(
                lambda: len(client.params_of(CodexRequestMethod.TURN_START)) == 2,
                failure="second start never reached provider",
            )
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
            await client.close_finished.wait()
            assert client.closes == 1
            assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []
            assert not client.turn_start_released.is_set()
        finally:
            client.turn_start_released.set()
            await room.adapter.backend_turns.drain()
            await room.adapter.on_cleanup(ROOM_ID)
