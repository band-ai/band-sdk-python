"""Observe the external peer while driving public adapter entry points."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from acp.schema import AllowedOutcome, RequestPermissionResponse, ToolCallUpdate
from pydantic import BaseModel

from band.adapters.antigravity_acp import AntigravityACPAdapter
from band.core.types import HistoryProvider
from band.testing import FakeAgentTools
from tests.adapters.antigravity_acp.peer import JOURNAL_FILE
from tests.adapters.antigravity_acp.peerconfig import PeerEvent
from tests.framework_conformance.turnprobes import (
    ALICE,
    ROOM_ID,
    DispatchingFakeTools,
    turn_input,
    user_message,
)
from tests.integrations.acp.clientbehavior.kit import antigravity_mcp_call


class ScheduleInput(BaseModel):
    """An application tool whose name overlaps a provider-native tool."""


def local_schedule(value: ScheduleInput) -> str:
    del value
    return "An application-controlled action."


def mcp_permission(server: str, tool: str) -> ToolCallUpdate:
    shape = antigravity_mcp_call(server, tool)
    return ToolCallUpdate(
        tool_call_id="c3-permission",
        title=shape["title"],
        raw_input=shape["raw_input"],
        field_meta=shape["meta"],
    )


@dataclass
class AntigravityRoom:
    adapter: AntigravityACPAdapter
    journal_root: Path

    def permission_choices(self, room_id: str = ROOM_ID) -> list[str | None]:
        outcomes = (
            RequestPermissionResponse.model_validate(record["response"]).outcome
            for record in self.records(PeerEvent.PERMISSION, room_id)
        )
        return [
            outcome.option_id if isinstance(outcome, AllowedOutcome) else None
            for outcome in outcomes
        ]

    def records(self, event: PeerEvent, room_id: str = ROOM_ID) -> list[dict[str, Any]]:
        path = self.journal_root / room_id / JOURNAL_FILE
        if not path.exists():
            return []
        return [
            record
            for line in path.read_text().splitlines()
            if (record := json.loads(line))["event"] == event
        ]

    async def send(
        self,
        *,
        room_id: str = ROOM_ID,
        content: str = "Please reply to this room.",
        history: list[dict[str, Any]] | None = None,
        tools: FakeAgentTools | None = None,
    ) -> FakeAgentTools:
        if tools is None:
            tools = DispatchingFakeTools(
                room_id=room_id,
                participants=[ALICE],
                agent_id=self.adapter.require_platform().agent_id,
            )
        message = replace(user_message(content), room_id=room_id)
        inp = replace(
            turn_input(tools, message), history=HistoryProvider(raw=history or [])
        )
        await self.adapter.on_event(inp)
        return tools
