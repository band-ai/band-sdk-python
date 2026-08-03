"""Cross-process event sharing for Claude Desktop room views."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.integrations.desktop_app import event_relay
from band.integrations.desktop_app.event_relay import DesktopRoomEventRelay


class FakeLink:
    """A Band WebSocket that stays up until the platform supersedes it."""

    def __init__(self) -> None:
        self.is_connected = True
        self.last_disconnect_reason: str | None = None
        self.disconnect = AsyncMock()

    def supersede(self) -> None:
        self.is_connected = False
        self.last_disconnect_reason = "superseded"


class FakePresence:
    def __init__(self, _: Any) -> None:
        self.on_room_event: Any = None
        self.on_room_joined: Any = None
        self.on_disconnected: Any = None
        self.start = AsyncMock()
        self.stop = AsyncMock()


class StalledFollower:
    """A follower that takes bytes but never lets the write complete.

    What a frozen Desktop looks like from the leader's end: the socket is open,
    so nothing raises, but its buffer never drains.
    """

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def write(self, payload: bytes) -> None:
        self.received.append(payload)

    async def drain(self) -> None:
        await asyncio.Event().wait()

    def close(self) -> None:
        pass


class HealthyFollower(StalledFollower):
    async def drain(self) -> None:
        return None


@dataclass
class RelayHarness:
    """Builds relays that share one agent key, recording their transports."""

    directory: Path
    links: list[FakeLink] = field(default_factory=list)
    presences: list[FakePresence] = field(default_factory=list)

    def build(self) -> DesktopRoomEventRelay:
        def link_factory(**_: Any) -> FakeLink:
            self.links.append(FakeLink())
            return self.links[-1]

        def presence_factory(link: Any) -> FakePresence:
            self.presences.append(FakePresence(link))
            return self.presences[-1]

        return DesktopRoomEventRelay(
            agent_id="agent-1",
            agent_key="secret",
            rest_url="https://platform.example",
            ws_url="wss://platform.example/socket",
            state_dir=self.directory,
            link_factory=link_factory,
            presence_factory=presence_factory,
        )


@pytest.fixture
def relays(monkeypatch: pytest.MonkeyPatch) -> Iterator[RelayHarness]:
    """A relay harness whose retry and health timers run at test speed."""
    monkeypatch.setattr(event_relay.RELAY_TUNING, "band_relay_retry_delay_s", 0.01)
    with tempfile.TemporaryDirectory(prefix="band-relay-", dir="/tmp") as directory:
        yield RelayHarness(Path(directory))


async def wait_until(predicate: Callable[[], bool], timeout: float = 2) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("room_id", ["room-1", "room-with-a-longer-id"])
async def test_one_websocket_owner_fans_events_out_and_fails_over(
    relays: RelayHarness,
    room_id: str,
) -> None:
    leader, follower = relays.build(), relays.build()
    await leader.start()
    await follower.start()
    try:
        assert len(relays.links) == 1
        assert (leader.status.role, follower.status.role) == ("leader", "follower")
        waits = [
            asyncio.create_task(
                relay.events.wait(
                    room_id,
                    after_version=relay.events.version(room_id),
                    timeout_seconds=1,
                )
            )
            for relay in (leader, follower)
        ]

        await relays.presences[0].on_room_event(room_id, MagicMock())

        assert await asyncio.gather(*waits) == [True, True]
        assert leader.status.events_received == 1
        assert follower.status.events_received == 1

        await leader.stop()
        await wait_until(lambda: len(relays.links) == 2)
        assert relays.presences[1].start.await_count == 1
    finally:
        await leader.stop()
        await follower.stop()


async def test_a_room_the_agent_joined_reaches_every_follower(
    relays: RelayHarness,
) -> None:
    """A follower holds no socket, so the leader is its only way to learn that
    another room now expects the agent — whenever it happened to connect."""
    leader, early = relays.build(), relays.build()
    await leader.start()
    await early.start()
    late = relays.build()
    try:
        await relays.presences[0].on_room_joined("room-9", {})
        await wait_until(lambda: early.status.rooms_added == ["room-9"])

        await late.start()

        await wait_until(lambda: late.status.rooms_added == ["room-9"])
        assert leader.status.rooms_added == ["room-9"]
    finally:
        await asyncio.gather(late.stop(), early.stop(), leader.stop())


async def test_failover_with_a_connected_follower_does_not_deadlock(
    relays: RelayHarness,
) -> None:
    """server.wait_closed() waits for the accept handlers on Python 3.12+,
    and a WebSocket failover never sets the relay-wide stop event they used
    to park on — the leader hung in its own cleanup still holding the leader
    lock, its followers wired to a dead socket indefinitely."""
    leader, follower = relays.build(), relays.build()
    await leader.start()
    await follower.start()
    try:
        relays.links[0].supersede()
        await relays.presences[0].on_disconnected()

        await wait_until(lambda: len(relays.links) == 2)
        await wait_until(lambda: leader.status.live)
        await wait_until(lambda: follower.status.role == "follower")
    finally:
        await asyncio.gather(follower.stop(), leader.stop())


async def test_wedged_followers_cost_one_fanout_timeout_not_one_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliveries run concurrently: fanout runs on the leader's WebSocket
    event path, so serial per-follower timeouts would let a few frozen
    Desktop processes starve every healthy one of events for their sum."""
    monkeypatch.setattr(event_relay.RELAY_TUNING, "band_relay_fanout_timeout_s", 0.2)
    healthy = HealthyFollower()
    clients = {StalledFollower(), StalledFollower(), healthy}

    started = asyncio.get_running_loop().time()
    await event_relay.fanout(clients, b"event room-1\n")
    elapsed = asyncio.get_running_loop().time() - started

    assert healthy.received == [b"event room-1\n"]
    assert clients == {healthy}, "the wedged followers lost their place"
    assert elapsed < 0.35, "two frozen followers were waited out one after the other"


async def test_a_superseded_websocket_is_replaced_rather_than_held(
    relays: RelayHarness,
) -> None:
    relay = relays.build()
    await relay.start()
    try:
        assert relay.status.live

        relays.links[0].supersede()
        # BandLink delivers the terminal disconnect through RoomPresence,
        # which the relay subscribes to; there is no polling to wait out.
        await relays.presences[0].on_disconnected()

        await wait_until(lambda: len(relays.links) == 2)
        await wait_until(lambda: relay.status.live)
        assert relays.presences[0].stop.await_count == 1
        assert relays.links[0].disconnect.await_count == 1
    finally:
        await relay.stop()


async def test_a_follower_that_stops_reading_is_dropped_not_waited_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fanout runs on the leader's WebSocket event path: one wedged follower
    must cost that follower its place, not cost every other one its events."""
    monkeypatch.setattr(event_relay.RELAY_TUNING, "band_relay_fanout_timeout_s", 0.05)
    follower = StalledFollower()

    async with asyncio.timeout(1):
        delivered = await event_relay.deliver(follower, b"event room-1\n")

    assert delivered is False, "a follower that never drains is reported unusable"
    assert follower.received == [b"event room-1\n"]


async def test_a_failed_leadership_claim_retries_instead_of_dying(
    relays: RelayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the lock file must not silently end event delivery for good."""
    failures = iter([OSError("lock file unavailable")])
    real_open = event_relay.os.open

    def flaky_open(*args: Any, **kwargs: Any) -> int:
        if (failure := next(failures, None)) is not None:
            raise failure
        return real_open(*args, **kwargs)

    monkeypatch.setattr(event_relay.os, "open", flaky_open)
    relay = relays.build()

    await relay.start()
    try:
        assert relay.status.live
    finally:
        await relay.stop()


async def test_a_relay_that_cannot_take_a_role_still_starts(
    relays: RelayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REST-backed room reads remain available while relay election retries."""
    monkeypatch.setattr(event_relay.RELAY_TUNING, "band_relay_start_timeout_s", 0.2)
    refused = ConnectionError("agent key already has a live consumer")
    relay = relays.build()

    with monkeypatch.context() as scoped:
        scoped.setattr(
            event_relay.DesktopRoomEventRelay,
            "_lead",
            AsyncMock(side_effect=refused),
        )
        await relay.start()
        try:
            assert not relay.status.live
            assert relay.status.warning
            assert str(refused) in str(relay.status.last_error)
        finally:
            await relay.stop()
