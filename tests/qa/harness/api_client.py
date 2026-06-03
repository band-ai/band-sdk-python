from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Global multiplier for all agent-wait timeouts. Set QA_TIMEOUT_SCALE=4 (etc.)
# to give slow adapters (claude_sdk, letta) much longer to respond without
# editing every scenario. Defaults to 1.0 (no change).
try:
    TIMEOUT_SCALE = float(os.environ.get("QA_TIMEOUT_SCALE", "1") or "1")
except ValueError:
    TIMEOUT_SCALE = 1.0
if TIMEOUT_SCALE != 1.0:
    logger.info("QA_TIMEOUT_SCALE=%s — scaling agent-wait timeouts", TIMEOUT_SCALE)


@dataclass
class AgentInfo:
    agent_id: str
    handle: str | None = None
    name: str | None = None


class PlatformClient:
    def __init__(self, base_url: str, user_api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "X-API-Key": user_api_key,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=30.0,
        )
        self._user_id: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def get_profile(self) -> dict:
        resp = await self._client.get("/api/v1/me/profile")
        resp.raise_for_status()
        data = resp.json()["data"]
        self._user_id = data["id"]
        return data

    async def get_user_id(self) -> str:
        if not self._user_id:
            await self.get_profile()
        assert self._user_id
        return self._user_id

    async def create_room(self) -> str:
        resp = await self._client.post(
            "/api/v1/me/chats",
            json={"chat": {}},
        )
        resp.raise_for_status()
        room_id = resp.json()["data"]["id"]
        logger.info("Created room %s", room_id)
        return room_id

    async def add_participant(self, room_id: str, agent_id: str) -> AgentInfo:
        resp = await self._client.post(
            f"/api/v1/me/chats/{room_id}/participants",
            json={"participant": {"participant_id": agent_id}},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        info = AgentInfo(
            agent_id=data["id"],
            handle=data.get("handle"),
            name=data.get("name"),
        )
        logger.info("Added participant %s (handle=%s)", info.agent_id, info.handle)
        return info

    async def remove_participant(self, room_id: str, participant_id: str) -> None:
        resp = await self._client.delete(
            f"/api/v1/me/chats/{room_id}/participants/{participant_id}",
        )
        resp.raise_for_status()

    async def list_participants(self, room_id: str) -> list[dict]:
        resp = await self._client.get(
            f"/api/v1/me/chats/{room_id}/participants",
        )
        resp.raise_for_status()
        return resp.json()["data"]

    async def send_message(
        self,
        room_id: str,
        content: str,
        agent: AgentInfo,
    ) -> dict:
        mention: dict[str, str] = {"id": agent.agent_id}
        if agent.handle:
            mention["handle"] = agent.handle
        if agent.name:
            mention["name"] = agent.name

        resp = await self._client.post(
            f"/api/v1/me/chats/{room_id}/messages",
            json={
                "message": {
                    "content": content,
                    "mentions": [mention],
                }
            },
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        logger.info("Sent message in room %s: %s", room_id, content[:80])
        return data

    async def get_messages(
        self,
        room_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict]:
        resp = await self._client.get(
            f"/api/v1/me/chats/{room_id}/messages",
            params={"page": page, "page_size": page_size},
        )
        resp.raise_for_status()
        return resp.json()["data"]

    async def wait_for_response(
        self,
        room_id: str,
        agent_id: str,
        after_message_id: str | None = None,
        timeout: float = 120.0,
        poll_interval: float = 2.0,
    ) -> dict | None:
        timeout *= TIMEOUT_SCALE
        start = time.monotonic()
        seen_ids: set[str] = set()

        while time.monotonic() - start < timeout:
            messages = await self.get_messages(room_id)
            for msg in messages:
                if msg.get("sender_id") == agent_id and msg["id"] not in seen_ids:
                    if after_message_id is None:
                        return msg
                    if msg.get("inserted_at", "") > "":
                        seen_ids.add(msg["id"])
                        if after_message_id and msg["id"] != after_message_id:
                            return msg
            for msg in messages:
                if (
                    msg.get("sender_id") == agent_id
                    and msg.get("sender_type") == "Agent"
                    and msg["id"] not in seen_ids
                ):
                    return msg
            seen_ids.update(m["id"] for m in messages)
            await asyncio.sleep(poll_interval)

        logger.warning("Timeout waiting for response from agent %s", agent_id)
        return None

    async def wait_for_agent_message(
        self,
        room_id: str,
        agent_id: str,
        known_message_ids: set[str],
        timeout: float = 120.0,
        poll_interval: float = 2.0,
    ) -> dict | None:
        timeout *= TIMEOUT_SCALE
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            messages = await self.get_messages(room_id)
            for msg in messages:
                if (
                    msg.get("sender_id") == agent_id
                    and msg.get("sender_type") == "Agent"
                    and msg["id"] not in known_message_ids
                    and msg.get("message_type") == "text"
                ):
                    return msg
            await asyncio.sleep(poll_interval)
        return None

    async def wait_for_agent_activity(
        self,
        room_id: str,
        agent_id: str,
        known_message_ids: set[str],
        timeout: float = 120.0,
        poll_interval: float = 2.0,
        settle_time: float = 5.0,
    ) -> dict | None:
        """Wait for agent activity — text message preferred, falls back to
        the most-recent tool_result if the agent responded with tools only.

        After first seeing new agent messages, waits *settle_time* extra
        seconds for a text follow-up before returning the best match.
        """
        timeout *= TIMEOUT_SCALE
        settle_time *= TIMEOUT_SCALE
        start = time.monotonic()
        first_activity_at: float | None = None
        best: dict | None = None

        while time.monotonic() - start < timeout:
            messages = await self.get_messages(room_id)
            text_hit: dict | None = None

            for msg in messages:
                if (
                    msg.get("sender_id") != agent_id
                    or msg.get("sender_type") != "Agent"
                    or msg["id"] in known_message_ids
                ):
                    continue

                known_message_ids.add(msg["id"])

                if msg.get("message_type") == "text" and text_hit is None:
                    text_hit = msg

                if first_activity_at is None:
                    first_activity_at = time.monotonic()

                if msg.get("message_type") == "tool_result":
                    best = msg

            if text_hit is not None:
                return text_hit

            if first_activity_at and (time.monotonic() - first_activity_at >= settle_time):
                break

            await asyncio.sleep(poll_interval)

        return best
