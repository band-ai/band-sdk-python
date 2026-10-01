"""Deterministic live-control runtime for baseline scenarios."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime

from band.client.streaming import AgentControlPayload, ControlMode
from band.platform.link import BandLink
from band.runtime.runtime import AgentRuntime
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)

# The runtime and execution loggers decide what a control signal does to a room.
SDK_CONTROL_LOGGER = "band.runtime"
SDK_LOG_TAIL_LINES = 60


class LogTail(logging.Handler):
    """The last lines a logger tree emitted, kept for a failure message."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: deque[str] = deque(maxlen=SDK_LOG_TAIL_LINES)

    def emit(self, record: logging.LogRecord) -> None:
        stamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S.%f")[
            :-3
        ]
        self.lines.append(f"{stamp} {record.name} {record.getMessage()}")


@contextmanager
def sdk_log_tail() -> Iterator[LogTail]:
    """Record the SDK's control-path logs at DEBUG for the block's duration."""
    tail = LogTail()
    sdk_logger = logging.getLogger(SDK_CONTROL_LOGGER)
    previous_level = sdk_logger.level
    sdk_logger.addHandler(tail)
    sdk_logger.setLevel(logging.DEBUG)
    try:
        yield tail
    finally:
        sdk_logger.removeHandler(tail)
        sdk_logger.setLevel(previous_level)


class ControlRuntime:
    """A real runtime with a handler that blocks its first cycle.

    The first execution remains in flight until a control signal cancels it;
    replayed work completes. This makes STOP -> PLAY observable without an LLM.
    """

    def __init__(self, *, log_tail: LogTail, block_cycles: int = 1) -> None:
        self._log_tail = log_tail
        self._block_cycles = block_cycles
        self._invocations = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.received_control_modes: list[ControlMode] = []
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
            raise TimeoutError(
                f"control signal did not cancel the active cycle; {self.diagnostics()}"
            ) from None

    def diagnostics(self) -> str:
        """What the SDK saw and did, for a control test's failure message."""
        modes = [mode.value for mode in self.received_control_modes]
        log = "\n".join(self._log_tail.lines) or "(none)"
        return (
            f"SDK received modes: {modes}; "
            f"handler completed messages: {self.completed_message_ids}; "
            f"SDK log tail:\n{log}"
        )

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
    link = BandLink(
        agent_id=agent.id,
        api_key=agent.api_key,
        ws_url=settings.endpoints.ws_url,
        rest_url=settings.endpoints.rest_url,
    )
    with sdk_log_tail() as log_tail:
        control = ControlRuntime(log_tail=log_tail)
        runtime = AgentRuntime(
            link=link, agent_id=agent.id, on_execute=control.on_execute
        )

        async def record_control(payload: AgentControlPayload) -> None:
            control.received_control_modes.append(payload.mode)
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
