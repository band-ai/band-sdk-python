"""Tests for AgentRunner and ThenvoiBridge (dumb-pipe behavior)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from bridge_core.bridge import AgentRunner, ThenvoiBridge
from bridge_core.config import BridgeConfig, ReconnectConfig
from bridge_core.forwarder import Forwarder
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


# ---------------------------------------------------------------------------
# AgentRunner — forwarder failure handling
# ---------------------------------------------------------------------------


class TestAgentRunnerForwarderFailures:
    async def test_forwarder_exception_does_not_crash_safe_handler(self) -> None:
        fwd = FakeForwarder()
        fwd.forward_side_effect = RuntimeError("network down")
        runner, _, _ = _build_runner(forwarder=fwd)

        # _safe_handle_event wraps _handle_event in a logging try/except.
        await runner._safe_handle_event(_make_message_event())
        # No exception means the failure was swallowed.


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
# ThenvoiBridge — orchestration
# ---------------------------------------------------------------------------


class TestThenvoiBridgeConstruction:
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

        bridge = ThenvoiBridge(config=config, forwarders=forwarders, links=links)

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

        bridge = ThenvoiBridge(
            config=config,
            forwarders={"a1": fwd_a, "a2": fwd_b},
            links=links,
        )

        runners_by_id = {r.agent_id: r for r in bridge.runners}
        assert runners_by_id["a1"].forwarder is fwd_a
        assert runners_by_id["a2"].forwarder is fwd_b


class TestThenvoiBridgeMultiIdentityIsolation:
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
        bridge = ThenvoiBridge(
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


class TestThenvoiBridgeShutdown:
    async def test_shutdown_closes_runners(self) -> None:
        fwd = FakeForwarder()
        link = make_link_mock()
        config = BridgeConfig(agents=[make_http_agent(agent_id="a1")])
        bridge = ThenvoiBridge(
            config=config, forwarders={"a1": fwd}, links={"a1": link}
        )

        await bridge._shutdown()

        link.disconnect.assert_awaited()
        assert fwd.closed is True
