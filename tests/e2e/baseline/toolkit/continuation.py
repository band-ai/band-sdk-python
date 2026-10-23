"""Hold real terminal confirmation while observing configured singleton retry."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from band.client.streaming import DeliveryStatus
from band.core.settlement import SettlementResult
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.control import (
    ControlLink,
    ControlRuntime,
    running_control_runtime,
)
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps


class FailureContinuation(ControlRuntime):
    """Fail once, then expose whether the retry followed terminal confirmation."""

    def __init__(self) -> None:
        super().__init__(block_cycles=0)
        self.failure_written = asyncio.Event()
        self.release_confirmation = asyncio.Event()
        self.failure_result: SettlementResult | None = None
        self.confirmed = False
        self.retained_failure = False
        self.retry_admitted_after_confirmation = False
        self.retry_executed_after_confirmation = False
        self.execution: ExecutionContext | None = None

    async def on_execute(self, ctx: ExecutionContext, event: PlatformEvent) -> None:
        if not isinstance(event, MessageEvent):
            return
        if self.execution is None:
            self.execution = ctx
            raise RuntimeError("intentional first-attempt failure")
        self.retry_executed_after_confirmation = self.confirmed

    async def wait_for_failure(self, *, deadline_s: float) -> None:
        async with asyncio.timeout(deadline_s):
            await self.failure_written.wait()


class ConfirmationLink(ControlLink):
    """Delay a real FAILED response without replacing platform acceptance."""

    control: FailureContinuation

    async def mark_failed_result(
        self, room_id: str, message_id: str, error: str | None = None
    ) -> SettlementResult:
        result = await super().mark_failed_result(room_id, message_id, error)
        control = self.control
        execution = control.execution
        pending = (
            execution.claims.pending_settlement(room_id, message_id)
            if execution is not None
            else None
        )
        control.retained_failure = (
            pending is not None and pending["status"] == DeliveryStatus.FAILED
        )
        control.failure_result = result
        control.failure_written.set()
        await control.release_confirmation.wait()
        control.confirmed = result is SettlementResult.ACCEPTED
        return result

    async def mark_processing_result(
        self, room_id: str, message_id: str
    ) -> SettlementResult:
        result = await super().mark_processing_result(room_id, message_id)
        if self.control.failure_written.is_set():
            self.control.retry_admitted_after_confirmation = (
                result is SettlementResult.ACCEPTED and self.control.confirmed
            )
        return result


@asynccontextmanager
async def running_failure_continuation(
    agent: ProvisionedAgent,
    room_id: str,
    settings: BaselineSettings,
    user_ops: UserOps,
) -> AsyncIterator[FailureContinuation]:
    control = FailureContinuation()
    async with running_control_runtime(
        agent,
        room_id,
        settings,
        user_ops,
        control=control,
        session_config=SessionConfig(max_message_retries=2, enable_working_state=False),
        link_factory=ConfirmationLink,
    ):
        try:
            yield control
        finally:
            control.release_confirmation.set()
