"""The common Room lifecycle suite's host and the contract each provider's
bridge fulfils.

A provider joins the suite from its own tests with a ``lifecycleprofile.py``
module exposing ``PROFILES``; the suite discovers it, so no provider edits
these shared files.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

from band.core.protocols import FrameworkAdapter
from band.core.retention import Residency, RetainedRoom
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from tests.adapters.hold import Hold
from tests.adapters.roompeer import STARTING_MARKER
from tests.adapters.waiting import until
from tests.framework_conformance.turnprobes import DispatchingFakeTools

IN_FLIGHT = frozenset({Residency.ACQUIRING, Residency.SUSPENDING})

# Modules that register a provider's profiles, each under its own tests.
PROFILE_MODULE = "lifecycleprofile.py"


async def pause_at(hold: Hold | None) -> None:
    """Stop at ``hold``, when set, until the test releases it."""
    if hold is not None:
        await hold.wait()


@dataclass(frozen=True)
class ExpectedFailure:
    """The exact error a provider reports: its type and message."""

    error: type[BaseException]
    message: str


@contextmanager
def raises_exactly(expected: ExpectedFailure) -> Iterator[None]:
    """Expect exactly ``expected``'s type, not a subclass, carrying its message."""
    with pytest.raises(expected.error, match=re.escape(expected.message)) as raised:
        yield
    assert raised.type is expected.error, raised.value


class LifecyclePeer(Protocol):
    """A provider's real child process with a held start or a failed or held
    close injected at its transport.

    ``refuse_close`` fails a close attempt and leaves the process running.
    Tests allow the close before the room retries it: a provider may retry by
    escalating past the refused seam, as ACP kills the process it still holds.
    """

    refuse_close: bool
    starting: Hold | None
    closing: Hold | None

    @property
    def exited(self) -> bool: ...

    async def close(self) -> None: ...


class LifecycleAdapter(FrameworkAdapter, Protocol):
    async def cleanup_all(self) -> None: ...


@dataclass(frozen=True)
class LifecycleBridge:
    """A started provider adapter and the projections the suite asserts on."""

    adapter: LifecycleAdapter
    # The room's retained residency, None once the room has none.
    residency: Callable[[str], RetainedRoom | None]
    # The room's provider conversation id, None once it has none.
    native_id: Callable[[str], str | None]


@dataclass(frozen=True)
class LifecycleProfile:
    """One provider configuration the common suite runs against.

    ``install`` builds the adapter on its production config with retention
    disabled, routing every child it starts through ``RetentionHost.spawned``.
    ``lost_turn`` is the error a turn whose transport is lost fails with, and
    ``failed_start`` what a turn whose start the peer refused reports, even
    when that failed start's close is refused too.
    """

    name: str
    install: Callable[[RetentionHost, pytest.MonkeyPatch], LifecycleBridge]
    lost_turn: type[BaseException]
    failed_start: ExpectedFailure


@dataclass
class RetentionHost:
    profile: LifecycleProfile
    root: Path
    fail_startup: Path
    hold_startup: Path
    shared: bool = True
    children: list[LifecyclePeer] = field(default_factory=list)
    child_cwds: list[str | None] = field(default_factory=list)
    # Children started while an earlier child in the same workspace still ran.
    overlaps: int = 0
    next_start: Hold | None = None
    bridge: LifecycleBridge = field(init=False)

    @property
    def peer_arguments(self) -> tuple[str, ...]:
        """Flags that let ``fail_startup``/``hold_startup`` steer the peer."""
        return (
            "--fail-startup",
            str(self.fail_startup),
            "--hold-startup",
            str(self.hold_startup),
        )

    @property
    def adapter(self) -> LifecycleAdapter:
        return self.bridge.adapter

    @property
    def lost_turn(self) -> type[BaseException]:
        return self.profile.lost_turn

    @property
    def failed_start(self) -> ExpectedFailure:
        return self.profile.failed_start

    def workspace(self, room_id: str) -> str:
        return str(self.root / ("shared" if self.shared else room_id))

    def spawned(self, child: LifecyclePeer, cwd: str | None) -> None:
        """Record a started child, handing it the next start hold."""
        child.starting, self.next_start = self.next_start, None
        self.overlaps += any(
            not earlier.exited and earlier_cwd == cwd
            for earlier, earlier_cwd in zip(self.children, self.child_cwds, strict=True)
        )
        self.children.append(child)
        self.child_cwds.append(cwd)

    async def send(self, room_id: str, content: str) -> list[str]:
        tools = DispatchingFakeTools(room_id=room_id)
        await self.deliver(room_id, content, tools)
        return [message["content"] for message in tools.messages_sent]

    async def deliver(
        self, room_id: str, content: str, tools: DispatchingFakeTools
    ) -> None:
        await self.adapter.on_event(
            AgentInput(
                msg=PlatformMessage(
                    id=content,
                    room_id=room_id,
                    content=content,
                    sender_id="user",
                    sender_type="User",
                    sender_name="Alice",
                    message_type="text",
                    metadata={},
                    created_at=datetime.now(UTC),
                ),
                tools=tools,
                history=HistoryProvider(raw=[]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id=room_id,
            )
        )

    async def leave(self, room_id: str) -> None:
        await self.adapter.on_cleanup(room_id)

    async def shutdown(self) -> None:
        await self.adapter.cleanup_all()

    def state(self, room_id: str) -> Residency | None:
        residency = self.bridge.residency(room_id)
        return residency.snapshot.state if residency is not None else None

    async def forgotten(self, room_id: str) -> None:
        """Wait until the adapter no longer holds the room, its disposal done."""
        await until(
            lambda: self.bridge.residency(room_id) is None,
            failure=f"Room {room_id} was never forgotten",
        )

    async def settled(self, room_id: str) -> None:
        """Wait out the room's in-flight process transition, such as recovery."""
        await until(
            lambda: self.state(room_id) not in IN_FLIGHT,
            failure=f"Room {room_id} never settled",
        )

    async def start_reached(self, room_id: str) -> None:
        """Wait until the room's peer has begun starting in its workspace."""
        marker = Path(self.workspace(room_id)) / STARTING_MARKER
        await until(
            marker.exists, failure=f"Room {room_id}'s peer never began starting"
        )

    def native_id(self, room_id: str) -> str | None:
        return self.bridge.native_id(room_id)

    @property
    def exited(self) -> list[bool]:
        return [child.exited for child in self.children]


@asynccontextmanager
async def hosted(
    profile: LifecycleProfile, root: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    """A started ``profile`` adapter whose children are released and closed on exit."""
    host = RetentionHost(
        profile=profile,
        root=root,
        fail_startup=root / "fail-startup",
        hold_startup=root / "hold-startup",
    )
    host.bridge = profile.install(host, monkeypatch)
    await host.adapter.on_started("Peer", "A subprocess under test")
    try:
        yield host
    finally:
        host.fail_startup.unlink(missing_ok=True)
        host.hold_startup.unlink(missing_ok=True)
        for child in host.children:
            child.refuse_close = False
            for barrier in (child.starting, child.closing):
                if barrier is not None:
                    barrier.released.set()
        try:
            await host.adapter.cleanup_all()
        finally:
            for child in host.children:
                await child.close()
