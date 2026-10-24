"""Exercise the production dispatcher without starting its background consumer."""

from __future__ import annotations

from dataclasses import asdict

from band.core.types import PlatformMessage as AdapterMessage
from band.platform.event import PlatformEvent, ReconnectedEvent
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from band.runtime.messages import PlatformMessage
from band.runtime.originals import backlog_event, is_ordinary
from band.runtime.selection import Candidate


async def attempt_delivery(
    ctx: ExecutionContext,
    source: PlatformEvent | PlatformMessage | AdapterMessage,
    *,
    priority: bool = False,
) -> TurnOutcome:
    if isinstance(source, AdapterMessage):
        source = PlatformMessage(**asdict(source))
    scheduler = ctx.scheduler
    if isinstance(source, ReconnectedEvent):
        scheduler.unpark()
        await scheduler.run_pass(recovery=True)
        return TurnOutcome.ADVANCED
    event = backlog_event(source) if isinstance(source, PlatformMessage) else source
    scheduler.pending.add(event)
    with scheduler.discovery():
        if is_ordinary(event):
            candidate = scheduler.classify(
                Candidate.listed(source)
                if isinstance(source, PlatformMessage)
                else Candidate.retained(event)
            )
            result = await scheduler.dispatch((candidate,), priority=priority)
        else:
            result = await scheduler.dispatch_local(event)
    assert result.turn is not None
    return result.turn
