"""Tests for AgentRunner and BandBridge (dumb-pipe behavior)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from bridge_core.bridge import AgentRunner, BandBridge
from bridge_core.config import BridgeConfig, ReconnectConfig
from bridge_core.forwarder import Forwarder
from thenvoi.runtime.types import PlatformMessage
from thenvoi.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
)

from .conftest import FakeForwarder, make_http_agent, make_link_mock


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def _make_message_event(
    message_id: str = "m1",
    room_id: str = "r1",
    sender_id: str = "sender",
    content: str = "hello",
) -> MessageEvent:
    payload = MagicMock()
    payload.id = message_id
    payload.content = content
    payload.sender_id = sender_id
    payload.model_dump = MagicMock(
        return_value={"id": message_id, "content": content, "sender_id": sender_id}
    )
    return MessageEvent(
        type="message_created",
        room_id=room_id,
        payload=payload,
        raw={"event": "message_created", "id": message_id},
    )


def _make_room_added_event(room_id: str = "r1") -> RoomAddedEvent:
    payload = MagicMock()
    payload.model_dump = MagicMock(return_value={"id": room_id})
    return RoomAddedEvent(
        type="room_added",
        room_id=room_id,
        payload=payload,
        raw={"event": "room_added"},
    )


def _make_room_removed_event(room_id: str = "r1") -> RoomRemovedEvent:
    payload = MagicMock()
    payload.model_dump = MagicMock(return_value={"id": room_id})
    return RoomRemovedEvent(
        type="room_removed",
        room_id=room_id,
        payload=payload,
        raw={"event": "room_removed"},
    )


def _make_participant_added_event(
    room_id: str = "r1", participant_id: str = "p1"
) -> ParticipantAddedEvent:
    payload = MagicMock()
    payload.id = participant_id
    payload.model_dump = MagicMock(return_value={"id": participant_id})
    return ParticipantAddedEvent(
        type="participant_added",
        room_id=room_id,
        payload=payload,
        raw={"event": "participant_added"},
    )


def _build_runner(
    *,
    agent_id: str = "agent-1",
    forwarder: Forwarder | None = None,
    link: MagicMock | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> tuple[AgentRunner, FakeForwarder, MagicMock]:
    fwd = forwarder if forwarder is not None else FakeForwarder()
    lnk = link if link is not None else make_link_mock()
    runner = AgentRunner(
        agent_config=make_http_agent(agent_id=agent_id),
        ws_url="wss://test",
        rest_url="https://test",
        forwarder=fwd,
        reconnect=ReconnectConfig(),
        shutdown_event=shutdown_event or asyncio.Event(),
        link=lnk,
    )
    return runner, fwd, lnk  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# AgentRunner — forwarding behavior
# ---------------------------------------------------------------------------


class TestAgentRunnerForwarding:
    async def test_forwards_message_event(self) -> None:
        runner, fwd, _ = _build_runner()
        await runner._handle_event(_make_message_event())

        assert isinstance(fwd, FakeForwarder)
        assert len(fwd.forwarded) == 1
        payload = fwd.forwarded[0]
        assert payload["event_type"] == "message_created"
        assert payload["agent_id"] == "agent-1"
        assert payload["room_id"] == "r1"
        assert payload["payload"]["id"] == "m1"
        assert payload["raw"]["id"] == "m1"
        assert "forwarded_at" in payload

    async def test_forwards_participant_added(self) -> None:
        runner, fwd, _ = _build_runner()
        await runner._handle_event(_make_participant_added_event())

        assert len(fwd.forwarded) == 1
        assert fwd.forwarded[0]["event_type"] == "participant_added"

    async def test_dedups_message_by_id(self) -> None:
        runner, fwd, _ = _build_runner()
        event = _make_message_event(message_id="m-dup")

        await runner._handle_event(event)
        await runner._handle_event(event)
        await runner._handle_event(event)

        assert len(fwd.forwarded) == 1

    async def test_dedup_does_not_cross_different_messages(self) -> None:
        runner, fwd, _ = _build_runner()

        await runner._handle_event(_make_message_event(message_id="m1"))
        await runner._handle_event(_make_message_event(message_id="m2"))

        assert len(fwd.forwarded) == 2

    async def test_does_not_filter_self_messages(self) -> None:
        """Dumb pipe: bridge forwards all messages, even from the agent itself.

        Self-message filtering is Band logic — it lives in the SDK inside the
        container, not in the bridge.
        """
        runner, fwd, _ = _build_runner(agent_id="agent-1")
        await runner._handle_event(_make_message_event(sender_id="agent-1"))

        assert len(fwd.forwarded) == 1


# ---------------------------------------------------------------------------
# AgentRunner — subscription management
# ---------------------------------------------------------------------------


class TestAgentRunnerSubscriptions:
    async def test_room_added_subscribes(self) -> None:
        runner, _, link = _build_runner()
        await runner._handle_event(_make_room_added_event(room_id="r-new"))

        link.subscribe_room.assert_awaited_once_with("r-new")

    async def test_room_removed_unsubscribes(self) -> None:
        runner, _, link = _build_runner()
        await runner._handle_event(_make_room_removed_event(room_id="r-gone"))

        link.unsubscribe_room.assert_awaited_once_with("r-gone")

    async def test_room_deleted_also_unsubscribes(self) -> None:
        runner, _, link = _build_runner()
        event = RoomDeletedEvent(
            type="room_deleted",
            room_id="r-deleted",
            payload=MagicMock(model_dump=MagicMock(return_value={})),
            raw={},
        )

        await runner._handle_event(event)

        link.unsubscribe_room.assert_awaited_once_with("r-deleted")

    async def test_subscribe_failure_is_logged_not_raised(self) -> None:
        runner, fwd, link = _build_runner()
        link.subscribe_room.side_effect = RuntimeError("subscribe failed")

        await runner._handle_event(_make_room_added_event())

        # Event is still forwarded even though subscribe failed.
        assert isinstance(fwd, FakeForwarder)
        assert len(fwd.forwarded) == 1

    async def test_room_removed_evicts_room_lock(self) -> None:
        """The per-room lock is dropped once the room is gone so ``_room_locks``
        does not grow unbounded over a long-lived bridge."""
        runner, _, _ = _build_runner()

        # A live message creates the lock.
        await runner._handle_event(_make_message_event(room_id="r-gone"))
        assert "r-gone" in runner._room_locks

        # Room teardown evicts it (after its own forward releases the lock).
        await runner._handle_event(_make_room_removed_event(room_id="r-gone"))
        assert "r-gone" not in runner._room_locks

    async def test_room_deleted_evicts_room_lock(self) -> None:
        runner, _, _ = _build_runner()
        await runner._handle_event(_make_message_event(room_id="r-del"))
        assert "r-del" in runner._room_locks

        event = RoomDeletedEvent(
            type="room_deleted",
            room_id="r-del",
            payload=MagicMock(model_dump=MagicMock(return_value={})),
            raw={},
        )
        await runner._handle_event(event)
        assert "r-del" not in runner._room_locks


# ---------------------------------------------------------------------------
# AgentRunner — forwarder failure handling
# ---------------------------------------------------------------------------


class TestAgentRunnerBackoff:
    """Regression for the jitter-is-a-bool bug: ``ReconnectConfig.jitter`` is a
    *fraction*, so 0.25 and 1.0 must produce visibly different sleep windows.
    """

    async def test_zero_jitter_is_fixed_delay(self) -> None:
        runner, _, _ = _build_runner()
        runner._reconnect = ReconnectConfig(jitter=0.0)
        # No randomness in the [0, 0] interval — always exactly delay.
        assert runner._backoff_sleep_seconds(8.0) == 8.0

    async def test_partial_jitter_clamps_to_min_window(self) -> None:
        runner, _, _ = _build_runner()
        runner._reconnect = ReconnectConfig(jitter=0.25)
        # 75% of delay is the fixed floor; only the top 25% randomizes.
        samples = [runner._backoff_sleep_seconds(8.0) for _ in range(200)]
        assert all(6.0 <= s <= 8.0 for s in samples)

    async def test_full_jitter_spans_zero_to_delay(self) -> None:
        runner, _, _ = _build_runner()
        runner._reconnect = ReconnectConfig(jitter=1.0)
        samples = [runner._backoff_sleep_seconds(8.0) for _ in range(200)]
        assert all(0.0 <= s <= 8.0 for s in samples)
        # With 200 samples on [0, 8] we should see something well below the
        # 0.25-jitter floor of 6.0 — the previous bool-treatment made these
        # two configs identical.
        assert min(samples) < 6.0


class TestAgentRunnerConcurrencyCap:
    """Regression: a burst of events must not fan unbounded concurrent
    forwards. With ``max_concurrent_forwards=N``, at most N forwards run at
    once even if N+K tasks are spawned together.
    """

    async def test_semaphore_caps_in_flight_forwards(self) -> None:
        in_flight = 0
        peak = 0
        gate = asyncio.Event()

        class _BlockingForwarder:
            async def forward(self, payload: dict[str, Any]) -> None:
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                try:
                    await gate.wait()
                finally:
                    in_flight -= 1

            async def close(self) -> None:
                return

        fwd = _BlockingForwarder()
        runner, _, _ = _build_runner(forwarder=fwd)  # type: ignore[arg-type]
        runner._forward_semaphore = asyncio.Semaphore(3)

        # Fire 10 forwards on distinct rooms so per-room locks don't serialize
        # them — only the semaphore can.
        tasks = [
            asyncio.create_task(
                runner._safe_handle_event(
                    _make_message_event(message_id=f"m{i}", room_id=f"r{i}")
                )
            )
            for i in range(10)
        ]
        # Let the event loop start them all.
        for _ in range(5):
            await asyncio.sleep(0)

        assert peak == 3, f"expected peak=3, saw {peak}"

        gate.set()
        await asyncio.gather(*tasks)


class TestAgentRunnerForwarderFailures:
    async def test_forwarder_exception_does_not_crash_safe_handler(self) -> None:
        fwd = FakeForwarder()
        fwd.forward_side_effect = RuntimeError("network down")
        runner, _, _ = _build_runner(forwarder=fwd)

        # _safe_handle_event wraps _handle_event in a logging try/except.
        await runner._safe_handle_event(_make_message_event())
        # No exception means the failure was swallowed.


# ---------------------------------------------------------------------------
# AgentRunner — startup rehydration
# ---------------------------------------------------------------------------


def _make_platform_message(
    message_id: str = "m1",
    room_id: str = "r1",
    sender_id: str = "sender",
    content: str = "hello",
) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name="Someone",
        message_type="user",
        metadata={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestAgentRunnerRehydration:
    async def test_forwards_one_nudge_per_room_with_backlog(self) -> None:
        runner, fwd, link = _build_runner()
        link.get_next_message.side_effect = [
            _make_platform_message("m-a", "r1"),
            _make_platform_message("m-b", "r2"),
        ]

        await runner._rehydrate_backlog(["r1", "r2"])

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-a", "m-b"}
        # Each forwarded nudge looks like a live message_created event.
        assert all(p["event_type"] == "message_created" for p in fwd.forwarded)

    async def test_skips_rooms_with_no_backlog(self) -> None:
        runner, fwd, link = _build_runner()
        link.get_next_message.return_value = None

        await runner._rehydrate_backlog(["r1", "r2"])

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []

    async def test_next_failure_does_not_block_other_rooms(self) -> None:
        runner, fwd, link = _build_runner()
        link.get_next_message.side_effect = [
            RuntimeError("boom"),
            _make_platform_message("m-b", "r2"),
        ]

        await runner._rehydrate_backlog(["r1", "r2"])

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-b"}

    async def test_rehydrated_message_dedups_against_live_event(self) -> None:
        runner, fwd, link = _build_runner()
        link.get_next_message.side_effect = [_make_platform_message("m-dup", "r1")]

        await runner._rehydrate_backlog(["r1"])
        # Same message id then arrives live on the WS.
        await runner._handle_event(
            _make_message_event(message_id="m-dup", room_id="r1")
        )

        assert isinstance(fwd, FakeForwarder)
        assert len(fwd.forwarded) == 1

    async def test_failed_forward_is_retryable_on_rehydration(self) -> None:
        """Regression: dedup must not mask a message whose first forward failed.

        If the bridge marks a message id as processed before the forward
        succeeds, a transient forwarder failure permanently swallows that
        message — on reconnect the rehydration sweep sees it again and drops
        it as a duplicate, so the room is stuck.
        """
        fwd = FakeForwarder()
        runner, _, link = _build_runner(forwarder=fwd)

        # First forward raises; second forward (after rehydration) succeeds.
        fwd.forward_side_effect = RuntimeError("network down")
        await runner._safe_handle_event(
            _make_message_event(message_id="m-stuck", room_id="r1")
        )
        assert fwd.forwarded == []  # forward failed → nothing recorded

        # Rehydration replays the same message id; bridge must attempt the
        # forward again rather than treat it as already-processed.
        fwd.forward_side_effect = None
        link.get_next_message.side_effect = [_make_platform_message("m-stuck", "r1")]
        await runner._rehydrate_backlog(["r1"])

        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-stuck"}


# ---------------------------------------------------------------------------
# AgentRunner — per-room serialization
# ---------------------------------------------------------------------------


class _OrderTrackingForwarder:
    """Forwarder that records start/end of each forward, with a delay.

    Lets us observe whether forwards serialize or overlap by examining the
    order of (start, end) events across calls.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self._delay = delay
        self.events: list[tuple[str, str]] = []  # (kind, msg_id)
        self.closed = False

    async def forward(self, payload: dict[str, Any]) -> None:
        msg_id = (payload.get("payload") or {}).get("id", "?")
        self.events.append(("start", msg_id))
        await asyncio.sleep(self._delay)
        self.events.append(("end", msg_id))

    async def close(self) -> None:
        self.closed = True


class TestAgentRunnerPerRoomSerialization:
    """Two events in the same room must serialize through the forwarder;
    two events in different rooms must overlap.

    Regression test for the duplicate-reply race seen during INT-506 deploy:
    two PA messages arriving back-to-back in the same room invoked the
    container twice in parallel; both invocations fetched history before
    either reply landed and both LLMs emitted duplicate responses.
    """

    async def test_same_room_serializes(self) -> None:
        fwd = _OrderTrackingForwarder(delay=0.05)
        runner, _, _ = _build_runner(forwarder=fwd)

        e1 = _make_message_event(message_id="m1", room_id="room-X")
        e2 = _make_message_event(message_id="m2", room_id="room-X")

        await asyncio.gather(
            runner._safe_handle_event(e1),
            runner._safe_handle_event(e2),
        )

        # With per-room serialization, one fully completes before the other
        # starts. Reject any interleaving.
        assert fwd.events in (
            [("start", "m1"), ("end", "m1"), ("start", "m2"), ("end", "m2")],
            [("start", "m2"), ("end", "m2"), ("start", "m1"), ("end", "m1")],
        ), f"Expected serial ordering, got: {fwd.events}"

    async def test_different_rooms_overlap(self) -> None:
        fwd = _OrderTrackingForwarder(delay=0.05)
        runner, _, _ = _build_runner(forwarder=fwd)

        e1 = _make_message_event(message_id="m1", room_id="room-A")
        e2 = _make_message_event(message_id="m2", room_id="room-B")

        await asyncio.gather(
            runner._safe_handle_event(e1),
            runner._safe_handle_event(e2),
        )

        # With different rooms, both forwards overlap: both start before
        # either ends.
        kinds = [e[0] for e in fwd.events]
        assert kinds == ["start", "start", "end", "end"], (
            f"Expected overlapping ordering, got: {fwd.events}"
        )


# ---------------------------------------------------------------------------
# BandBridge — orchestration
# ---------------------------------------------------------------------------


class TestBandBridgeConstruction:
    def test_builds_runner_per_agent(self) -> None:
        config = BridgeConfig(
            agents=[
                make_http_agent(agent_id="a1", url="https://x/y"),
                make_http_agent(agent_id="a2", url="https://y/z"),
            ]
        )
        links = {"a1": make_link_mock(), "a2": make_link_mock()}
        forwarders: dict[str, Forwarder] = {
            "a1": FakeForwarder(),
            "a2": FakeForwarder(),
        }

        bridge = BandBridge(config=config, forwarders=forwarders, links=links)

        assert len(bridge.runners) == 2
        ids = {r.agent_id for r in bridge.runners}
        assert ids == {"a1", "a2"}

    def test_each_runner_gets_its_own_forwarder(self) -> None:
        config = BridgeConfig(
            agents=[
                make_http_agent(agent_id="a1"),
                make_http_agent(agent_id="a2"),
            ]
        )
        fwd_a = FakeForwarder()
        fwd_b = FakeForwarder()
        links = {"a1": make_link_mock(), "a2": make_link_mock()}

        bridge = BandBridge(
            config=config,
            forwarders={"a1": fwd_a, "a2": fwd_b},
            links=links,
        )

        runners_by_id = {r.agent_id: r for r in bridge.runners}
        assert runners_by_id["a1"].forwarder is fwd_a
        assert runners_by_id["a2"].forwarder is fwd_b


class TestBandBridgeMultiIdentityIsolation:
    """Regression test for the kill-shot single-identity bug: a message in
    a shared room must be forwardable to each agent that participates.
    """

    async def test_each_runner_forwards_independently(self) -> None:
        config = BridgeConfig(
            agents=[
                make_http_agent(agent_id="a1"),
                make_http_agent(agent_id="a2"),
            ]
        )
        fwd_a = FakeForwarder()
        fwd_b = FakeForwarder()
        bridge = BandBridge(
            config=config,
            forwarders={"a1": fwd_a, "a2": fwd_b},
            links={"a1": make_link_mock(), "a2": make_link_mock()},
        )

        runners_by_id = {r.agent_id: r for r in bridge.runners}

        # Simulate the same message being delivered on a2's WS subscription
        # (which would happen if a1 sent it in a room where both participate).
        msg = _make_message_event(sender_id="a1", message_id="m1")
        await runners_by_id["a2"]._handle_event(msg)

        # a2's forwarder received it. The bridge does NOT filter self-messages
        # by sender_id (that's the SDK's job inside the container).
        assert len(fwd_b.forwarded) == 1
        assert fwd_b.forwarded[0]["agent_id"] == "a2"
        assert fwd_b.forwarded[0]["payload"]["sender_id"] == "a1"

        # a1's runner only sees events its own subscription delivers; in this
        # test we routed the event to a2 only, so a1's forwarder is empty.
        assert len(fwd_a.forwarded) == 0


class TestBandBridgeShutdown:
    async def test_shutdown_closes_runners(self) -> None:
        fwd = FakeForwarder()
        link = make_link_mock()
        config = BridgeConfig(agents=[make_http_agent(agent_id="a1")])
        bridge = BandBridge(config=config, forwarders={"a1": fwd}, links={"a1": link})

        await bridge._shutdown()

        link.disconnect.assert_awaited()
        assert fwd.closed is True
