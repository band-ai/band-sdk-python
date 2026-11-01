"""Real activity and confirmation-message HTTP held at the platform boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from http import HTTPStatus

import httpx

from band.adapters.opencode.adapter import TurnState
from band.adapters.opencode.approvals.room import RoomApprovals
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.adapters.opencode.helpers import FakeOpencodeClient
from tests.runtime.activityownership.kit import message_content
from tests.runtime.helpers import is_message_post
from tests.runtime.permissionactivity.kit import RoomActivityPlatform

ORIGIN_MESSAGE_ID = "original-request"
ORIGIN_CAP_SECONDS = 5


class ConfirmationPlatform(RoomActivityPlatform):
    def __init__(self) -> None:
        super().__init__()
        self.notice: str | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.status = HTTPStatus.CREATED
        self.confirmed_notices: list[str] = []

    async def respond(self, request: httpx.Request) -> httpx.Response:
        is_event = request.method == "POST" and request.url.path.endswith("/events")
        content = (
            message_content(request)
            if is_message_post(request)
            else json.loads(request.content)["event"]["content"]
            if is_event
            else None
        )
        if content is not None and self.notice is not None and content == self.notice:
            self.entered.set()
            await self.release.wait()
            if self.status != HTTPStatus.CREATED:
                return httpx.Response(self.status, json={"error": "Notice rejected"})
            self.confirmed_notices.append(content)
            if is_event:
                return httpx.Response(
                    HTTPStatus.CREATED,
                    json={
                        "data": {
                            "id": "notice",
                            "message_type": "error",
                            "success": True,
                        }
                    },
                )
        return await super().respond(request)


@dataclass(frozen=True)
class NoticeRoom:
    approvals: RoomApprovals
    platform: ConfirmationPlatform
    execution: ExecutionContext
    tools: AgentTools
    client: FakeOpencodeClient
    turn: TurnState
    delivery: asyncio.Task[TurnOutcome]


OpenNoticeRoom = Callable[..., AbstractAsyncContextManager[NoticeRoom]]
