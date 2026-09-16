"""Deterministic live-control runtime for baseline scenarios."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from band.platform.link import BandLink
from band.runtime.runtime import AgentRuntime

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


class ControlRuntime:
    """A real runtime with a handler that blocks its first cycle.

    The first execution remains in flight until a control signal cancels it;
    replayed work completes. This makes STOP -> PLAY observable without an LLM.
    """

    def __init__(self, *, block_cycles: int = 1) -> None:
        self._block_cycles = block_cycles
        self._invocations = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.completed_message_ids: list[str] = []

    async def on_execute(self, _ctx: object, event: object) -> None:
        self._invocations += 1
        message_id = getattr(getattr(event, "payload", None), "id", None)
        self.started.set()
        try:
            if self._invocations <= self._block_cycles:
                await asyncio.Future[None]()
            if message_id is not None:
                self.completed_message_ids.append(message_id)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def wait_for_cancellation(self, *, deadline_s: float) -> None:
        try:
            await asyncio.wait_for(self.cancelled.wait(), timeout=deadline_s)
        except TimeoutError:
            raise TimeoutError("STOP did not cancel the active cycle") from None

    async def wait_for_start(self, *, deadline_s: float) -> None:
        try:
            await asyncio.wait_for(self.started.wait(), timeout=deadline_s)
        except TimeoutError:
            raise TimeoutError("message never entered the active cycle") from None


@asynccontextmanager
async def running_control_runtime(
    agent: ProvisionedAgent,
    room_id: str,
    settings: BaselineSettings,
    user_ops: UserOps,
) -> AsyncGenerator[ControlRuntime, None]:
    """Run one controlled agent and leave its room playable on teardown."""
    control = ControlRuntime()
    link = BandLink(
        agent_id=agent.id,
        api_key=agent.api_key,
        ws_url=settings.endpoints.ws_url,
        rest_url=settings.endpoints.rest_url,
    )
    runtime = AgentRuntime(link=link, agent_id=agent.id, on_execute=control.on_execute)
    link.on_control = runtime.handle_control
    await runtime.start()
    try:
        yield control
    finally:
        try:
            await user_ops.play_agent(room_id)
        except Exception:  # noqa: BLE001 - teardown must still release the runtime
            logger.warning(
                "control cleanup play failed for room %s", room_id, exc_info=True
            )
        await runtime.stop()
