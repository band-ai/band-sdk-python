"""An approved turn held at its resumed computation boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from band.adapters.opencode import OpencodeAdapterConfig
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.dispatch import deliver_message
from tests.adapters.hold import Hold
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    ObservedOpencodeAdapter,
    event_message_updated,
    event_permission,
    event_session_idle,
    event_text_part,
    make_platform_message,
    run_single_turn,
    tools_protocol,
)
from tests.adapters.opencode.ownership import AdapterOwner
from tests.toolkit.timecontrol import ControlledClock, controlled_clock

COMPUTE_BUDGET_S = 8.0
REPLY = "Approved and done."


@dataclass
class ApprovalTurn:
    clock: ControlledClock
    client: FakeOpencodeClient
    tools: FakeAgentTools
    adapter: ObservedOpencodeAdapter
    finishing: Hold
    watcher: asyncio.Task[None]

    @property
    def aborted_sessions(self) -> list[str]:
        return self.client.aborted_sessions

    @property
    def reply(self) -> str:
        return self.tools.chat[-1]

    @property
    def failures(self) -> list[tuple[str, str]]:
        return failure_reports(self.tools)

    @property
    def complete(self) -> bool:
        return self.tools.turn.complete

    async def approve(self) -> None:
        await deliver_message(
            self.adapter,
            make_platform_message(content="approve req-1"),
            tools_protocol(FakeAgentTools()),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await self.finishing.arrived()

    async def finish(self) -> None:
        self.finishing.released.set()
        await self.watcher


@asynccontextmanager
async def pending_approval() -> AsyncIterator[ApprovalTurn]:
    finishing = Hold()
    client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_permission("sess-1", "req-1"),
            ]
        ],
        reply_permission_events={
            "req-1": [
                finishing.wait,
                event_text_part("sess-1", "msg-1", REPLY),
                event_session_idle("sess-1"),
            ]
        },
    )
    async with controlled_clock() as clock, AdapterOwner() as adapters:
        adapter = adapters.create(
            client,
            config=OpencodeAdapterConfig(
                approval_mode="manual",
                turn_timeout_s=COMPUTE_BUDGET_S,
                approval_wait_timeout_s=64.0,
            ),
        )
        tools = FakeAgentTools()
        try:
            await run_single_turn(adapter, tools, content="run it")
            await adapter.watcher_started.wait()
            turn = adapter._rooms["room-1"].turn
            assert turn is not None and turn.watcher is not None
            yield ApprovalTurn(clock, client, tools, adapter, finishing, turn.watcher)
        finally:
            finishing.released.set()
