"""Deterministic live-control runtime for baseline scenarios."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import band.runtime
from band.client.streaming import (
    AgentControlPayload,
    ControlMode,
    MessageCreatedPayload,
)
from band.platform.event import MessageEvent, PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.types import SessionConfig
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.logs import sdk_logs_at
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


class ControlRuntime:
    """A real runtime with a handler that blocks its first cycle.

    The first execution remains in flight until a control signal cancels it;
    replayed work completes. This makes STOP -> PLAY observable without an LLM.
    """

    def __init__(
        self, *, block_cycles: int = 1, withheld_message_marker: str | None = None
    ) -> None:
        self._block_cycles = block_cycles
        self._invocations = 0
        self.started = asyncio.Event()
        self.startup_synced = asyncio.Event()
        self.invoked_message_ids: list[str] = []
        self.cancelled = asyncio.Event()
        self.received_control_modes: list[ControlMode] = []
        self.completed_message_ids: list[str] = []
        self.withheld_message_marker = withheld_message_marker
        self.withheld_message_ids: list[str] = []
        self.message_withheld = asyncio.Event()
        self.control_received = asyncio.Event()

    async def wait_for_withheld_message(self, *, deadline_s: float) -> None:
        async with asyncio.timeout(deadline_s):
            await self.message_withheld.wait()

    async def wait_for_control(self, mode: ControlMode, *, deadline_s: float) -> None:
        async with asyncio.timeout(deadline_s):
            while mode not in self.received_control_modes:
                self.control_received.clear()
                await self.control_received.wait()

    async def on_execute(self, _ctx: ExecutionContext, event: PlatformEvent) -> None:
        self._invocations += 1
        message_id = getattr(getattr(event, "payload", None), "id", None)
        if message_id is not None:
            self.invoked_message_ids.append(message_id)
        self.started.set()
        try:
            if self._invocations <= self._block_cycles:
                await asyncio.Future[None]()
            if message_id is not None:
                self.completed_message_ids.append(message_id)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def wait_for_startup_sync(self, *, deadline_s: float) -> None:
        async with asyncio.timeout(deadline_s):
            await self.startup_synced.wait()

    async def wait_for_cancellation(self, *, deadline_s: float) -> None:
        try:
            await asyncio.wait_for(self.cancelled.wait(), timeout=deadline_s)
        except TimeoutError:
            raise TimeoutError(
                f"control signal did not cancel the active cycle; {self.diagnostics()}"
            ) from None

    def diagnostics(self) -> str:
        """What the SDK saw and did, for a control test's failure message."""
        modes = [mode.value for mode in self.received_control_modes]
        return (
            f"SDK received modes: {modes}; "
            f"handler completed messages: {self.completed_message_ids}"
        )

    async def wait_for_start(self, *, deadline_s: float) -> None:
        try:
            await asyncio.wait_for(self.started.wait(), timeout=deadline_s)
        except TimeoutError:
            raise TimeoutError("message never entered the active cycle") from None


class AuxiliaryClaimRuntime(ControlRuntime):
    """Select two auxiliary messages, holding the second claim for a real STOP."""

    def __init__(self) -> None:
        super().__init__(block_cycles=0)
        self.auxiliaries: asyncio.Queue[str] = asyncio.Queue()
        self.auxiliary_ids: list[str] = []
        self.release_claim = asyncio.Event()
        self.downstream_started = asyncio.Event()
        self.owned_ids: set[str] = set()

    async def on_execute(self, ctx: ExecutionContext, event: PlatformEvent) -> None:
        assert isinstance(event, MessageEvent)
        self.invoked_message_ids.append(event.payload.id)
        self.started.set()
        first = not self.auxiliary_ids
        cancelled = False
        try:
            for index in range(2):
                if first:
                    mid = await self.auxiliaries.get()
                    self.auxiliary_ids.append(mid)
                else:
                    mid = self.auxiliary_ids[index]
                assert ctx.claims.try_claim(ctx.room_id, mid), "auxiliary already owned"
                self.owned_ids.add(mid)
                if first and index == 1:
                    await self.release_claim.wait()
                assert await ctx.claim_message(mid), "auxiliary claim failed"
            self.downstream_started.set()
            for mid in self.auxiliary_ids:
                ctx.claims.remember_ack_pending(ctx.room_id, mid)
                assert await ctx.link.mark_processed(ctx.room_id, mid)
                ctx.claims.remember_completed(ctx.room_id, mid)
            self.completed_message_ids.append(event.payload.id)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            for mid in self.owned_ids:
                ctx.claims.release(ctx.room_id, mid)
            self.owned_ids.clear()
            if cancelled:
                self.cancelled.set()


class ObservedExecution(ExecutionContext):
    """Expose completion of the real startup synchronization to live tests."""

    startup_synced: asyncio.Event

    async def _synchronize_with_next(self) -> bool:
        synced = await super()._synchronize_with_next()
        if synced:
            self.startup_synced.set()
        return synced


class ControlLink(BandLink):
    """Withhold a selected push while keeping the real transports active."""

    control: ControlRuntime
    control_room_id: str

    async def _on_message_created(
        self, room_id: str, payload: MessageCreatedPayload
    ) -> None:
        if (
            room_id == self.control_room_id
            and self.control.withheld_message_marker is not None
            and self.control.withheld_message_marker in payload.content
        ):
            self.control.withheld_message_ids.append(payload.id)
            self.control.message_withheld.set()
            return
        await super()._on_message_created(room_id, payload)


@asynccontextmanager
async def running_control_runtime(
    agent: ProvisionedAgent,
    room_id: str,
    settings: BaselineSettings,
    user_ops: UserOps,
    *,
    block_cycles: int = 1,
    control: ControlRuntime | None = None,
    forwarded_control_modes: frozenset[ControlMode] = frozenset(ControlMode),
) -> AsyncGenerator[ControlRuntime, None]:
    """Run one controlled agent and leave its room playable on teardown."""
    if control is None:
        control = ControlRuntime(block_cycles=block_cycles)
    link = ControlLink(
        agent_id=agent.id,
        api_key=agent.api_key,
        ws_url=settings.endpoints.ws_url,
        rest_url=settings.endpoints.rest_url,
    )
    link.control = control
    link.control_room_id = room_id
    # The SDK control path's DEBUG lines explain a failing control test.
    with sdk_logs_at(band.runtime, logging.DEBUG):

        def execution_factory(
            room: str, link: BandLink, *, hub_room_id: str | None = None
        ) -> ExecutionContext:
            execution = ObservedExecution(
                room,
                link,
                control.on_execute,
                config=SessionConfig(idle_resync_seconds=1, idle_resync_max_seconds=2),
                agent_id=agent.id,
                hub_room_id=hub_room_id,
            )
            execution.startup_synced = (
                control.startup_synced if room == room_id else asyncio.Event()
            )
            return execution

        runtime = AgentRuntime(
            link=link,
            agent_id=agent.id,
            on_execute=control.on_execute,
            execution_factory=execution_factory,
        )

        async def record_control(payload: AgentControlPayload) -> None:
            control.received_control_modes.append(payload.mode)
            control.control_received.set()
            if payload.mode in forwarded_control_modes:
                await runtime.handle_control(payload)

        link.on_control = record_control
        await runtime.start()
        try:
            yield control
        finally:
            try:
                await user_ops.play_agent(room_id)
            except Exception:
                logger.warning(
                    "control cleanup play failed for room %s", room_id, exc_info=True
                )
            await runtime.stop()
