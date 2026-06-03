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

    @staticmethod
    def _event_tool_name(content: object) -> str | None:
        """Extract the tool name from a tool_call/tool_result event payload.

        Platform events serialise the tool name under the ``tool`` key, e.g.
        ``{"tool": "band_get_participants", "input": {...}}``. Older payloads
        used ``name``; accept either for robustness.
        """
        if isinstance(content, dict):
            return content.get("tool") or content.get("name")
        return None

    def _matches(self, content: object, tool_name: str) -> bool:
        if self._event_tool_name(content) == tool_name:
            return True
        return isinstance(content, str) and tool_name in content

    async def assert_tool_called(self, room_id: str, tool_name: str) -> bool:
        for ev in await self.get_tool_events(room_id):
            if ev["message_type"] == "tool_call" and self._matches(ev["content"], tool_name):
                return True
        return False

    async def assert_tool_result(self, room_id: str, tool_name: str) -> bool:
        for ev in await self.get_tool_events(room_id):
            if ev["message_type"] == "tool_result" and self._matches(ev["content"], tool_name):
                return True
        return False

    async def get_tool_call_count(self, room_id: str, tool_name: str) -> int:
        return sum(
            1
            for ev in await self.get_tool_events(room_id)
            if ev["message_type"] == "tool_call" and self._matches(ev["content"], tool_name)
        )
