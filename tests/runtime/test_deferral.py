"""Unaccepted work shares the host scheduler with active-turn controls."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from band.core.exceptions import InputNotAccepted
from band.core.simple_adapter import SimpleAdapter
from band.platform.event import ReconnectedEvent
from band.platform.link import BandLink
from band.runtime.oneshot import OneShotInvoker, OneShotStatus
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
    synthetic_message,
)


class ApprovalAdapter(SimpleAdapter[Any]):
    """A detached turn owns its tools until its approval is consumed."""

    def __init__(self) -> None:
        super().__init__()
        self.active_tools: Any = None
        self.completed: list[str] = []
        self.attempted: list[str] = []
        self.controls: list[str] = []
        self.reject = False

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        if msg.content != "approve":
            return False
        self.controls.append(msg.id)
        if self.active_tools is not None:
            self.active_tools.turn.settle()
            self.completed.append("A")
            self.active_tools = None
        return True

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        self.attempted.append(msg.id)
        if self.active_tools is not None or self.reject:
            raise InputNotAccepted()
        if msg.id == "A":
            tools.turn.detach()
            self.active_tools = tools
        else:
            self.completed.append(msg.id)
            tools.turn.settle()


def envelope(message_id: str) -> dict[str, Any]:
    return {
        "event_type": "message_created",
        "room_id": ROOM_ID,
        "payload": {
            "id": message_id,
            "content": "approve" if message_id == "C" else "ordinary",
            "sender_id": "user-1",
            "sender_type": "User",
            "message_type": "text",
            "inserted_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    }


@pytest.mark.parametrize("hosting", ["live", "recovery"])
async def test_unaccepted_work_logs_why_it_remains_pending(
    hosting: str, caplog: pytest.LogCaptureFixture
) -> None:
    peer = LifecyclePlatform()
    peer.add_message("B")
    adapter = ApprovalAdapter()
    adapter.reject = True
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            adapter,
            link,
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        with caplog.at_level(logging.DEBUG, logger="band.runtime.execution"):
            if hosting == "live":
                await ctx._process_event(
                    make_message_event(room_id=ROOM_ID, msg_id="B")
                )
            else:
                await ctx._reconcile()
        await ctx.stop()

    assert peer.marked("processed") == peer.marked("failed") == []
    rejection = next(
        (record for record in caplog.records if "not accepted" in record.message),
        None,
    )
    assert rejection is not None, "Outstanding work needs a rejection diagnostic"
    assert rejection.levelno == logging.DEBUG


@pytest.mark.parametrize("recovery", ["live", "missed", "reconnect", "startup"])
async def test_approval_beyond_first_page_releases_ordered_work(recovery: str) -> None:
    peer = LifecyclePlatform()
    peer.add_message("A")
    adapter = ApprovalAdapter()
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            adapter,
            link,
            config=SessionConfig(
                enable_context_hydration=False,
                enable_working_state=False,
                idle_resync_seconds=0.01,
                idle_resync_max_seconds=0.01,
                max_message_retries=1,
            ),
        )
        if recovery == "startup":
            peer.add_message("B")
        await ctx.start()
        traffic = asyncio.Event()

        async def ordinary_traffic() -> None:
            while not traffic.is_set():
                await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="D0"))
                await asyncio.sleep(0.001)

        traffic_task: asyncio.Task[None] | None = None
        try:
            await wait_for_condition(lambda: peer.marked("processed") == ["A"])
            original_tools = adapter.active_tools
            if recovery != "startup":
                peer.add_message("B")
            await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="B"))
            await wait_for_condition(lambda: "B" in adapter.attempted)
            assert peer.marked("processed") == ["A"]
            assert peer.marked("failed") == []
            # More than one chronological page hides a missed approval from /next.
            for i in range(100):
                peer.add_message(f"D{i}")
            peer.add_message("C")
            peer.messages[-1]["content"] = "approve"
            if recovery == "live":
                await ctx.on_event(
                    make_message_event(room_id=ROOM_ID, msg_id="C", content="approve")
                )
            elif recovery == "reconnect":
                await ctx.on_event(ReconnectedEvent())
            # Sustained ordinary delivery cannot rewind an in-progress scan.
            traffic_task = asyncio.create_task(ordinary_traffic())
            for i in range(5):
                await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id=f"D{i}"))
            await wait_for_condition(
                lambda: "D99" in peer.marked("processed"), timeout=3
            )
            assert original_tools.turn.complete
            assert adapter.completed == ["A", "B", *[f"D{i}" for i in range(100)]]
            assert adapter.controls == ["C"]
            assert peer.marked("processed") == [
                "A",
                "C",
                "B",
                *[f"D{i}" for i in range(100)],
            ]
            assert peer.marked("failed") == []
            assert any(cursor is not None for cursor in peer.actionable_cursors)
            # A terminal duplicate cannot reopen or reapply the approval.
            await ctx.on_event(
                make_message_event(room_id=ROOM_ID, msg_id="C", content="approve")
            )
            await asyncio.sleep(0)
            assert adapter.controls == ["C"]
        finally:
            traffic.set()
            if traffic_task is not None:
                await traffic_task
            await ctx.stop()


async def test_synthetic_work_survives_stop_and_rejection_in_receive_order() -> None:
    peer = LifecyclePlatform(stopped=True)
    adapter = ApprovalAdapter()
    adapter.reject = True
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            adapter,
            link,
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        ctx.stop_room()
        for message_id in ["local-B", "local-D"]:
            ctx._enqueue_synthetic(synthetic_message(message_id))
        assert adapter.completed == []
        peer.stopped = False
        await ctx.resume_room()
        await ctx._reconcile()
        assert adapter.completed == []
        adapter.reject = False
        await ctx._reconcile()
        assert adapter.completed == ["local-B", "local-D"]
        assert peer.requested_marks == []
        await ctx.stop()


async def test_oneshot_control_only_acknowledges_itself_and_rejection_never_drains() -> (
    None
):
    peer = LifecyclePlatform()
    peer.add_message("B")
    peer.add_message("C")
    peer.messages[-1]["content"] = "approve"
    adapter = ApprovalAdapter()
    adapter.reject = True
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
        await invoker.startup()
        result = await invoker.handle_event(envelope("B"))
        assert result["status"] == OneShotStatus.NOT_ACCEPTED
        assert peer.marked("processed") == peer.marked("failed") == []
        result = await invoker.handle_event(envelope("C"))
        assert result["status"] == OneShotStatus.DONE
        assert peer.marked("processed") == ["C"]
        assert "drained" not in result
        assert adapter.attempted == ["B"]
        # Neither a duplicate control nor the old ordinary snapshot can drain B.
        await invoker.handle_event(envelope("C"))
        assert adapter.controls == ["C"]
        assert [message["id"] for message in peer.messages] == ["B"]


async def test_stop_during_synthetic_execution_retains_work_for_play() -> None:
    peer = LifecyclePlatform()
    entered = asyncio.Event()
    blocker = asyncio.Event()

    class InterruptibleAdapter(ApprovalAdapter):
        async def on_message(
            self, msg: Any, tools: Any, *args: Any, **kwargs: Any
        ) -> None:
            if not entered.is_set():
                entered.set()
                await blocker.wait()
            await super().on_message(msg, tools, *args, **kwargs)

    adapter = InterruptibleAdapter()
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            adapter,
            link,
            config=SessionConfig(
                enable_context_hydration=False,
                enable_working_state=False,
                idle_resync_seconds=0.01,
                idle_resync_max_seconds=0.01,
            ),
        )
        await ctx.start()
        try:
            await ctx.on_event(synthetic_message("local-B"))
            await entered.wait()
            peer.stopped = True
            ctx.stop_room()
            await wait_for_condition(lambda: ctx._active_cycle_task is None)
            assert adapter.completed == []
            peer.stopped = False
            await ctx.resume_room()
            await wait_for_condition(lambda: adapter.completed == ["local-B"])
            assert peer.requested_marks == []
        finally:
            await ctx.stop()


async def test_busy_attempts_preserve_previous_execution_failure_budget() -> None:
    peer = LifecyclePlatform()
    peer.add_message("B")
    mode = "failure"
    invoked: list[str] = []

    class FailingAdapter(ApprovalAdapter):
        async def on_message(
            self, msg: Any, tools: Any, *args: Any, **kwargs: Any
        ) -> None:
            invoked.append(mode)
            if mode == "busy":
                raise InputNotAccepted()
            raise OSError("provider transport uncertainty")

    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            FailingAdapter(),
            link,
            config=SessionConfig(
                enable_context_hydration=False,
                enable_working_state=False,
                max_message_retries=2,
                report_turn_failures_to_room=False,
            ),
        )
        event = make_message_event(room_id=ROOM_ID, msg_id="B")
        await ctx._process_event(event)
        mode = "busy"
        for _ in range(5):
            await ctx._reconcile()
        assert peer.marked("failed") == ["B"]
        assert peer.marked("processed") == []
        mode = "failure"
        await ctx._reconcile()
        await ctx._reconcile()
        assert peer.marked("failed") == ["B", "B"]
        assert invoked == ["failure", *["busy"] * 5, "failure"]
        await ctx.stop()


async def test_missed_play_releases_busy_work_at_existing_reconciliation_boundary() -> (
    None
):
    peer = LifecyclePlatform()
    peer.add_message("B")
    adapter = ApprovalAdapter()
    adapter.reject = True
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            adapter,
            link,
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        await ctx._reconcile()
        ctx.stop_room()
        peer.stopped = True
        await ctx._reconcile()
        assert peer.marked("processed") == []
        # PLAY was lost on the socket; /next's accepted gate discovers it.
        peer.stopped = False
        adapter.reject = False
        peer.add_message("D")
        await ctx._reconcile()
        assert adapter.completed == ["B", "D"]
        assert peer.marked("processed") == ["B", "D"]
        assert peer.marked("failed") == []
        await ctx.stop()


async def test_stale_processed_control_cannot_be_reopened_by_oneshot() -> None:
    peer = LifecyclePlatform()
    peer.add_message("C")
    peer.messages[-1]["metadata"] = {
        "delivery_status": {AGENT_ID: {"status": "processed"}}
    }
    adapter = ApprovalAdapter()
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
        await invoker.startup()
        result = await invoker.handle_event(envelope("C"))
        assert result["status"] == OneShotStatus.ALREADY_PROCESSED
        assert adapter.controls == adapter.attempted == []
        assert peer.requested_marks == []


@pytest.mark.parametrize("hosting", ["runtime", "oneshot"])
async def test_real_control_failure_keeps_existing_failed_delivery_policy(
    hosting: str,
) -> None:
    peer = LifecyclePlatform()
    peer.add_message("B")
    peer.add_message("C")
    peer.messages[-1]["content"] = "approve"

    class BrokenControlAdapter(ApprovalAdapter):
        async def try_handle_control(self, msg: Any, tools: Any) -> bool:
            if msg.content == "approve":
                raise OSError("control transport failed")
            return False

    adapter = BrokenControlAdapter()
    adapter.reject = True
    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        if hosting == "runtime":
            ctx = run_through(
                adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_message_retries=1,
                    report_turn_failures_to_room=False,
                ),
            )
            await ctx._reconcile()
            await ctx._reconcile()
            assert peer.marked("failed") == ["C"]
            await ctx.stop()
        else:
            invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
            await invoker.startup()
            with pytest.raises(OSError, match="control transport failed"):
                await invoker.handle_event(envelope("C"))
            assert peer.marked("failed") == ["C"]
        assert peer.marked("processed") == []
        assert "C" not in adapter.attempted


async def test_stop_between_control_discovery_and_claim_prevents_provider_effects() -> (
    None
):
    peer = LifecyclePlatform()
    peer.add_message("B")
    peer.add_message("C")
    peer.messages[-1]["content"] = "approve"
    adapter = ApprovalAdapter()
    adapter.reject = True

    def stopped_control_gate(request: Any) -> Any:
        response = peer.answer(request)
        if request.url.path.endswith("/messages"):
            peer.stopped = True
        return response

    async with rest_client_over(stopped_control_gate) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            adapter,
            link,
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        await ctx._reconcile()
        assert adapter.controls == []
        assert peer.requested("processing") == ["B", "C"]
        assert peer.marked("processing") == ["B"]
        assert peer.marked("processed") == peer.marked("failed") == []
        await ctx.stop()
