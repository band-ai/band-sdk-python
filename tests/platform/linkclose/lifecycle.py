"""A live BandLink driven step by step over a real (fake-server-backed) wire,
for the model-based lifecycle test: the steps a platform or host can take,
and the bookkeeping the invariants are checked against."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from enum import StrEnum

from hypothesis import strategies as st
from websockets.frames import CloseCode

from band.client.streaming.client import AgentControlPayload, WireEvent
from band.core.exceptions import AgentDisconnectedError
from band.platform.event import WebSocketDisconnectedEvent
from band.platform.link import BandLink
from band.testing import FakePhoenixServer, close_transport, fake_phoenix_server
from tests.conftest import TERMINAL_SUPERSEDE, spy_on_reconciliation_drain
from tests.platform.linkclose.kit import (
    CONTROL_TOPIC,
    control_wire_payload,
    make_link,
)

# A healthy step takes milliseconds; a hung one fails its example within this.
STEP_GUARD_S = 2.0


class Step(StrEnum):
    SERVER_NORMAL_CLOSE = "server closes 1000"
    SERVER_GOING_AWAY = "server closes 1001"
    CLIENT_NORMAL_CLOSE = "client closes normally"
    NETWORK_DROP = "network drops"
    CONTROL = "platform sends a control signal"
    HELD_CONTROL = "platform sends a control signal the adapter is slow to apply"
    RELEASE_HELD_CONTROL = "adapter finishes the slow control signal"
    SUPERSEDE = "platform supersedes the session"
    HOST_CANCELS_RUN = "host gives up waiting on run_forever"
    HOST_RUNS_AGAIN = "host whose run returned calls run_forever again"


sequences = st.lists(st.sampled_from(Step), max_size=6)


@dataclass
class Tally:
    """Correlation ids seen so far, in order, and a wake-up for a waiter."""

    ids: list[str] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def add(self, correlation_id: str) -> None:
        self.ids.append(correlation_id)
        self.changed.set()

    async def until(self, correlation_id: str) -> None:
        while correlation_id not in self.ids:
            self.changed.clear()
            await self.changed.wait()


def tally_arrivals(link: BandLink) -> Tally:
    """Tally each control signal once the link has queued it on its lane.
    Arm before ``connect()``, which binds the handler."""
    arrived = Tally()
    on_control = link._on_control

    async def spy(payload: AgentControlPayload) -> None:
        await on_control(payload)
        assert payload.correlation_id is not None
        arrived.add(payload.correlation_id)

    link._on_control = spy
    return arrived


@dataclass
class LinkSession:
    """One connected link, its running host, and what it has seen."""

    server: FakePhoenixServer
    link: BandLink
    run: asyncio.Task[None]
    arrived: Tally
    ended: bool = False
    connections_at_end: int = 0
    pushed: list[str] = field(default_factory=list)
    applied: Tally = field(default_factory=Tally)
    hooks_running: int = 0
    held_id: str | None = None
    release_held: asyncio.Event = field(default_factory=asyncio.Event)
    held_entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def hook(self, payload: AgentControlPayload) -> None:
        self.hooks_running += 1
        try:
            if payload.correlation_id == self.held_id:
                self.held_entered.set()
                await self.release_held.wait()
            assert payload.correlation_id is not None
            self.applied.add(payload.correlation_id)
        finally:
            self.hooks_running -= 1

    async def take(self, step: Step) -> None:
        if self.ended:
            # The session is over: its socket and topics are gone, and only
            # the host can still act.
            match step:
                case Step.HOST_CANCELS_RUN:
                    await self.host_cancels_run()
                case Step.HOST_RUNS_AGAIN:
                    await self.host_runs_again()
            return
        match step:
            case Step.SERVER_NORMAL_CLOSE:
                await self.reconnect_after(
                    self.server.close_connection(code=CloseCode.NORMAL_CLOSURE)
                )
            case Step.SERVER_GOING_AWAY:
                await self.reconnect_after(
                    self.server.close_connection(code=CloseCode.GOING_AWAY)
                )
            case Step.CLIENT_NORMAL_CLOSE:
                await self.reconnect_after(close_transport(self.link))
            case Step.NETWORK_DROP:
                await self.reconnect_after(self.server.abort_connection())
            case Step.CONTROL:
                await self.send_control(held=False)
            case Step.HELD_CONTROL:
                await self.send_control(held=self.held_id is None)
            case Step.RELEASE_HELD_CONTROL:
                await self.release_held_control()
            case Step.SUPERSEDE:
                await self.supersede()
            case Step.HOST_CANCELS_RUN | Step.HOST_RUNS_AGAIN:
                # A live session's run never returns, so the host can only
                # give up on it.
                await self.host_cancels_run()

    async def reconnect_after(self, close: Awaitable[None]) -> None:
        reconnected = spy_on_reconciliation_drain(self.link)
        await close
        await asyncio.wait_for(reconnected.wait(), STEP_GUARD_S)

    async def send_control(self, *, held: bool) -> None:
        correlation_id = f"control-{len(self.pushed)}"
        if held:
            self.held_id = correlation_id
        self.pushed.append(correlation_id)
        await self.server.push(
            CONTROL_TOPIC, WireEvent.AGENT_CONTROL, control_wire_payload(correlation_id)
        )
        # Queued behind a held signal, it is in the lane before the next step.
        await asyncio.wait_for(self.arrived.until(correlation_id), STEP_GUARD_S)
        if held:
            await asyncio.wait_for(self.held_entered.wait(), STEP_GUARD_S)
        elif self.held_id is None:
            await asyncio.wait_for(self.applied.until(correlation_id), STEP_GUARD_S)

    async def release_held_control(self) -> None:
        """Let the slow signal finish; the lane then applies everything pushed
        behind it, in the order it was pushed."""
        if self.held_id is None:
            return
        self.release_held.set()
        await asyncio.wait_for(self.applied.until(self.pushed[-1]), STEP_GUARD_S)
        assert self.applied.ids == self.pushed
        self.held_id = None
        self.held_entered.clear()
        self.release_held.clear()

    async def supersede(self) -> None:
        await self.server.push(CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE)
        while not isinstance(
            await asyncio.wait_for(anext(self.link), STEP_GUARD_S),
            WebSocketDisconnectedEvent,
        ):
            pass
        self.ended = True
        self.connections_at_end = self.server.connection_count

    async def host_runs_again(self) -> None:
        await asyncio.wait_for(asyncio.wait({self.run}), STEP_GUARD_S)
        with suppress(AgentDisconnectedError):
            # A run that surfaced its terminal cause has ended the session
            # exactly as returning does; the typed raise is the contract.
            await asyncio.wait_for(
                self.link.run_forever(install_signal_handlers=False), STEP_GUARD_S
            )

    async def host_cancels_run(self) -> None:
        with suppress(TimeoutError, AgentDisconnectedError):
            async with asyncio.timeout(0):
                await self.link.run_forever(install_signal_handlers=False)


@asynccontextmanager
async def link_session() -> AsyncIterator[LinkSession]:
    async with fake_phoenix_server() as server:
        link = make_link(server.url)
        arrived = tally_arrivals(link)
        await link.connect()
        run = asyncio.create_task(link.run_forever(install_signal_handlers=False))
        session = LinkSession(server=server, link=link, run=run, arrived=arrived)
        link.on_control = session.hook
        try:
            yield session
        finally:
            session.release_held.set()
            run.cancel()
            await asyncio.wait({run})
            with suppress(Exception):
                run.result()
            with suppress(Exception):
                await link.disconnect()
