"""Real context hydration over the recovery peer's lifecycle surface."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from band.core.types import delivery_status_for_agent
from band.runtime.execution import ExecutionContext
from tests.platform.settlement.kit import Reply
from tests.runtime.helpers import AGENT_ID
from tests.runtime.recovery.kit import ROOM_PATH, RoomPeer


class HistoryRoomPeer(RoomPeer):
    def __init__(self) -> None:
        super().__init__()
        self.context_requests = 0
        self.app.add_route(ROOM_PATH + "/context", self.context, methods=["GET"])

    async def context(self, request: Request) -> Response:
        self.context_requests += 1
        return self.reply_with(
            Reply.of_json(
                status=200,
                payload={
                    "data": [self.wire(row) for row in self.rows.values()],
                    "metadata": {"has_more": False, "limit": 100},
                },
            )
        )


async def history_statuses(ctx: ExecutionContext) -> dict[str, str | None]:
    context = await ctx.get_context()
    return {
        message["id"]: delivery_status_for_agent(message.get("metadata"), AGENT_ID)
        for message in context.messages
    }
