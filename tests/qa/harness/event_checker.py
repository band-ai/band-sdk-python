from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api_client import PlatformClient

logger = logging.getLogger(__name__)


class EventChecker:
    def __init__(self, client: PlatformClient) -> None:
        self._client = client

    async def get_tool_events(self, room_id: str) -> list[dict]:
        messages = await self._client.get_messages(room_id, page_size=100)
        events: list[dict] = []
        for msg in messages:
            mt = msg.get("message_type", "text")
            if mt not in ("tool_call", "tool_result"):
                continue
            raw = msg.get("content", "")
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": raw}
            events.append({
                "id": msg["id"],
                "message_type": mt,
                "sender_id": msg.get("sender_id"),
                "content": parsed,
                "raw": raw,
            })
        return events

    async def assert_tool_called(self, room_id: str, tool_name: str) -> bool:
        for ev in await self.get_tool_events(room_id):
            if ev["message_type"] != "tool_call":
                continue
            c = ev["content"]
            if isinstance(c, dict) and c.get("name") == tool_name:
                return True
            if isinstance(c, str) and tool_name in c:
                return True
        return False

    async def assert_tool_result(self, room_id: str, tool_name: str) -> bool:
        for ev in await self.get_tool_events(room_id):
            if ev["message_type"] != "tool_result":
                continue
            c = ev["content"]
            if isinstance(c, dict) and c.get("name") == tool_name:
                return True
            if isinstance(c, str) and tool_name in c:
                return True
        return False

    async def get_tool_call_count(self, room_id: str, tool_name: str) -> int:
        count = 0
        for ev in await self.get_tool_events(room_id):
            if ev["message_type"] != "tool_call":
                continue
            c = ev["content"]
            if isinstance(c, dict) and c.get("name") == tool_name:
                count += 1
            elif isinstance(c, str) and tool_name in c:
                count += 1
        return count
