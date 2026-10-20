from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from band.adapters.crewai_flow import CrewAIFlowAdapter
from band.core.types import PlatformMessage
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.hold import Hold
from tests.adapters.waiting import until


def empty_flow() -> MagicMock:
    return MagicMock()


def message(*, id: str = "msg-1", content: str = "hi") -> PlatformMessage:
    return PlatformMessage(
        id=id,
        room_id="room-1",
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Pat",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


@dataclass
class QueuedFlows:
    room_id: str = field(default_factory=lambda: uuid4().hex)
    first_id: str = field(default_factory=lambda: uuid4().hex)
    second_id: str = field(default_factory=lambda: uuid4().hex)
    third_id: str = field(default_factory=lambda: uuid4().hex)
    first: Hold = field(default_factory=Hold)
    second: Hold = field(default_factory=Hold)
    calls: list[str] = field(default_factory=list)
    active: int = 0
    peak_concurrency: int = 0

    def factory(self) -> QueuedFlow:
        return QueuedFlow(self)


class QueuedFlow:
    def __init__(self, work: QueuedFlows) -> None:
        self.work = work

    async def kickoff_async(self, inputs: dict[str, Any]) -> dict[str, str]:
        message_id = inputs["message"]["id"]
        self.work.calls.append(message_id)
        self.work.active += 1
        self.work.peak_concurrency = max(self.work.peak_concurrency, self.work.active)
        try:
            hold = {
                self.work.first_id: self.work.first,
                self.work.second_id: self.work.second,
            }.get(message_id)
            if hold is not None:
                await hold.wait()
            return {"decision": "waiting", "reason": message_id}
        finally:
            self.work.active -= 1


async def deliver(
    adapter: CrewAIFlowAdapter,
    tools: FakeAgentTools,
    work: QueuedFlows,
    message_id: str,
) -> None:
    await adapter.on_message(
        msg=replace(message(id=message_id), room_id=work.room_id),
        tools=tools,
        history=None,
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=work.room_id,
    )


async def queued_callers(
    adapter: CrewAIFlowAdapter, room_id: str, *, count: int
) -> None:
    entry = adapter._room_locks[room_id]
    await until(
        lambda: entry.active == count,
        failure="Room operations did not reach their shared lock",
    )
