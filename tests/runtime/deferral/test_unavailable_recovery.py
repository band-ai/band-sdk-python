"""Unavailable input stays failed when provider recovery overtakes its wake."""

from __future__ import annotations

from contextlib import ExitStack

import pytest

from band.client.streaming import DeliveryStatus
from band.core.turn_lifecycle import BackendTurns
from band.platform.link import BandLink
from band.runtime.runtime import AgentRuntime
from tests.core.recordingcancel import RecordingCancel
from tests.runtime.conftest import Waiting
from tests.runtime.deferral.kit import FailureReportGate, RecoveringAdapter
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    PostingPlatform,
    no_periodic_wake_config,
    rest_client_over,
    run_through,
    running_execution,
)


@pytest.mark.looptime
class TestUnavailableRecovery:
    async def test_recovery_while_refusal_report_waits_cannot_replay_the_original(
        self,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        handle = object()
        async with turns.running(ROOM_ID, handle):
            await turns.stop(ROOM_ID)
        adapter = RecoveringAdapter(turns)
        peer = PostingPlatform()
        report = FailureReportGate(peer)
        async with rest_client_over(report.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            config = no_periodic_wake_config()
            config.max_message_retries = 3
            ctx = run_through(adapter, link, config=config)
            async with running_execution(ctx):
                with ExitStack() as cleanup:
                    cleanup.callback(report.release.set)
                    peer.add_message("old")
                    await ctx.on_event(peer.event("old"))
                    await Waiting.until(report.entered.is_set)
                    turns.mark_unavailable(ROOM_ID)
                    turns.confirm_stopped(ROOM_ID, handle)
                    report.release.set()
                    await adapter.refused.wait()
                    await Waiting.suspended(ctx)
                    assert peer.marked("failed") == ["old"]
                    peer.add_message("fresh")
                    await ctx.on_event(peer.event("fresh"))
                    await Waiting.until(lambda: peer.marked("processed") == ["fresh"])

        assert adapter.completed == ["fresh"]

    async def test_new_room_latches_unavailable_arrival_before_its_first_turn(
        self,
        recovering_runtime: tuple[
            AgentRuntime, BackendTurns[object], RecoveringAdapter, RestPeer
        ],
    ) -> None:
        runtime, turns, adapter, peer = recovering_runtime
        handle = object()
        async with turns.running(ROOM_ID, handle):
            await turns.stop(ROOM_ID)
        turns.mark_unavailable(ROOM_ID)
        ctx = await runtime.rooms.admit(ROOM_ID)
        assert ctx is not None
        await ctx.on_event(peer.post("during-outage"))
        turns.confirm_stopped(ROOM_ID, handle)
        await peer.write_received(DeliveryStatus.FAILED, "during-outage")
        await ctx.on_event(peer.post("fresh"))
        await peer.write_received(DeliveryStatus.PROCESSED, "fresh")
        # Outage arrival fails terminally without provider invoke; only fresh runs.
        assert adapter.completed == ["fresh"]
        assert (
            peer.status_of("during-outage"),
            peer.status_of("fresh"),
        ) == (DeliveryStatus.FAILED, DeliveryStatus.PROCESSED)

    @pytest.mark.parametrize("batching", [False, True])
    async def test_recovery_before_readiness_recheck_does_not_execute_failed_input(
        self,
        batching: bool,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        handle = object()
        async with turns.running(ROOM_ID, handle):
            await turns.stop(ROOM_ID)
        adapter = RecoveringAdapter(turns)
        peer = RestPeer()
        peer.post("old")
        peer.post("also-old")
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            config = no_periodic_wake_config()
            config.consume_all_queued = batching
            ctx = run_through(adapter, link, config=config)
            async with running_execution(ctx):
                await ctx.on_event(peer.replay("old"))
                await ctx.on_event(peer.replay("also-old"))
                await adapter.refused.wait()
                await Waiting.suspended(ctx)

                # No yield: settlement overtakes the scheduler's readiness wake.
                turns.mark_unavailable(ROOM_ID)
                await ctx.on_event(peer.post("during-outage"))
                turns.confirm_stopped(ROOM_ID, handle)
                await peer.write_received(DeliveryStatus.FAILED, "old")
                await Waiting.suspended(ctx)
                assert peer.status_of("also-old") is DeliveryStatus.FAILED
                assert peer.status_of("during-outage") is DeliveryStatus.FAILED
                await ctx.on_event(peer.post("fresh"))
                await peer.write_received(DeliveryStatus.PROCESSED, "fresh")
                await ctx.on_event(peer.replay("old"))
                await ctx.request_resync()
                await Waiting.suspended(ctx)

        assert adapter.completed == ["fresh"]
        assert (
            peer.status_of("old"),
            peer.status_of("also-old"),
            peer.status_of("fresh"),
        ) == (
            DeliveryStatus.FAILED,
            DeliveryStatus.FAILED,
            DeliveryStatus.PROCESSED,
        )

    async def test_failed_terminal_write_retries_without_executing_recovered_input(
        self,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        handle = object()
        async with turns.running(ROOM_ID, handle):
            await turns.stop(ROOM_ID)
        adapter = RecoveringAdapter(turns)
        peer = RestPeer()
        peer.confirm_terminal = False
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with running_execution(ctx):
                await ctx.on_event(peer.post("old"))
                await adapter.refused.wait()
                await Waiting.suspended(ctx)
                turns.mark_unavailable(ROOM_ID)
                turns.confirm_stopped(ROOM_ID, handle)
                await peer.write_received(DeliveryStatus.FAILED, "old", confirmed=False)
                await Waiting.suspended(ctx)
                assert adapter.completed == []

                peer.confirm_terminal = True
                await ctx.request_resync()
                await peer.write_received(DeliveryStatus.FAILED, "old")
                await ctx.on_event(peer.post("fresh"))
                await peer.write_received(DeliveryStatus.PROCESSED, "fresh")

        assert adapter.completed == ["fresh"]
        assert (peer.status_of("old"), peer.status_of("fresh")) == (
            DeliveryStatus.FAILED,
            DeliveryStatus.PROCESSED,
        )

    async def test_queue_only_input_is_latched_when_unavailable_absorbs(
        self,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        handle = object()
        async with turns.running(ROOM_ID, handle):
            await turns.stop(ROOM_ID)
        adapter = RecoveringAdapter(turns)
        peer = RestPeer()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with running_execution(ctx):
                await ctx.on_event(peer.post("old"))
                await adapter.refused.wait()
                await Waiting.suspended(ctx)
                assert not turns.unavailable(ROOM_ID)

                # Enqueue while available so receive does not latch; keep the
                # current task running through mark_unavailable so absorb is
                # the only path that sees the id before the scheduler wakes.
                queued = peer.post("queue-only")
                ctx.delivery.receive(queued)
                assert peer.status_of("queue-only") is None
                ctx.queue.put_nowait(queued)
                turns.mark_unavailable(ROOM_ID)
                turns.confirm_stopped(ROOM_ID, handle)
                await peer.write_received(DeliveryStatus.FAILED, "old")
                await peer.write_received(DeliveryStatus.FAILED, "queue-only")
                await Waiting.suspended(ctx)
                assert peer.status_of("queue-only") is DeliveryStatus.FAILED
                assert "queue-only" not in adapter.completed
                await ctx.on_event(peer.post("fresh"))
                await peer.write_received(DeliveryStatus.PROCESSED, "fresh")

        assert adapter.completed == ["fresh"]

    async def test_control_arrival_during_unavailability_is_not_latched(
        self,
        recovering_runtime: tuple[
            AgentRuntime, BackendTurns[object], RecoveringAdapter, RestPeer
        ],
    ) -> None:
        runtime, turns, adapter, peer = recovering_runtime
        adapter.control_content = "approve"
        handle = object()
        async with turns.running(ROOM_ID, handle):
            await turns.stop(ROOM_ID)
        turns.mark_unavailable(ROOM_ID)
        ctx = await runtime.rooms.admit(ROOM_ID)
        assert ctx is not None
        await ctx.on_event(peer.post("during-outage"))
        await ctx.on_event(peer.post("approve"))
        await peer.write_received(DeliveryStatus.FAILED, "during-outage")
        await peer.write_received(DeliveryStatus.PROCESSED, "approve")
        assert peer.status_of("approve") is DeliveryStatus.PROCESSED
        assert adapter.controls == ["approve"]
        assert "approve" not in adapter.completed
        turns.confirm_stopped(ROOM_ID, handle)
        await ctx.on_event(peer.post("fresh"))
        await peer.write_received(DeliveryStatus.PROCESSED, "fresh")
        assert adapter.completed == ["fresh"]

    async def test_partial_backend_turns_stand_in_does_not_half_wire(self) -> None:
        peer = RestPeer()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                RecoveringAdapter(
                    BackendTurns(
                        RecordingCancel(), provider="test", requires_confirmation=True
                    )
                ),
                link,
                config=no_periodic_wake_config(),
            )

            class OnlyLatch:
                def __init__(self) -> None:
                    self.bound = False

                def add_failure_latch(self, latch: object) -> None:
                    self.bound = True

            only = OnlyLatch()
            ctx.delivery.bind_backend_turns(only)
            assert only.bound is False
