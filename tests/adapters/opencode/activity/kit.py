"""Owned OpenCode turns and source-ordered external event barriers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from band.adapters.opencode import OpencodeAdapter
from band.integrations.opencode import OpencodeToolStatus
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.hold import Hold
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    RawOpencodeEvent,
    ServerStep,
    event_tool_part,
)

SESSION = "sess-1"
MESSAGE = "native-message"
CALL = "native-call"
NATIVE_TOOL = "bash"


async def publish_events(client: FakeOpencodeClient, *events: ServerStep) -> None:
    barrier = Hold()
    try:
        for event in events:
            await client.push_event(event)
        await client.push_event(barrier.wait)
        await barrier.arrived()
    finally:
        barrier.released.set()


def native_part(
    status: OpencodeToolStatus,
    *,
    call_id: str = CALL,
    name: str = NATIVE_TOOL,
    message_id: str = MESSAGE,
) -> RawOpencodeEvent:
    return event_tool_part(
        SESSION,
        message_id,
        tool=name,
        call_id=call_id,
        status=status,
        input_data={"private": "hidden argument"},
        output="hidden result",
        part_id=f"part-{call_id}",
    )


@dataclass(frozen=True)
class ActivityRoom:
    adapter: OpencodeAdapter
    client: FakeOpencodeClient
    tools: FakeAgentTools
    recorder: ActivityRecorder

    @property
    def prompt_id(self) -> str:
        value = self.client.prompt_calls[-1]["message_id"]
        assert isinstance(value, str) and value
        return value

    def registered_tool_name(self, name: str) -> str:
        registration = self.client.registered_mcp_servers[-1]["name"]
        return f"{registration}_{name}"

    async def publish(self, *events: ServerStep) -> None:
        await publish_events(self.client, *events)


OpenActivityRoom = Callable[..., AbstractAsyncContextManager[ActivityRoom]]
