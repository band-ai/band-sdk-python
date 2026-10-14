"""A stateful Band REST peer for several rooms, behind the real Fern client."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.scenarios.observations import Observations

AGENT_ID = "agent-scenario"
STAMP = "2026-01-01T00:00:00Z"
API_PREFIX = "/api/v1/agent/"


def room_json(room_id: str) -> dict[str, Any]:
    return {
        "id": room_id,
        "title": f"Room {room_id}",
        "inserted_at": STAMP,
        "updated_at": STAMP,
    }


def message_json(room_id: str, message_id: str, content: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "chat_room_id": room_id,
        "content": content,
        "sender_id": "user-1",
        "sender_type": "User",
        "sender_name": "User",
        "message_type": "text",
        "metadata": {"mentions": [{"id": AGENT_ID, "username": "agent"}]},
        "inserted_at": STAMP,
        "updated_at": STAMP,
    }


@dataclass
class PeerRoom:
    """One room as the platform holds it."""

    member: bool = True
    stopped: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: dict[str, str] = field(default_factory=dict)
    next_reads: int = 0

    def outstanding(self) -> list[dict[str, Any]]:
        """Messages the agent has not finished, oldest first."""
        return [
            m
            for m in self.messages
            if self.status.get(m["id"]) not in ("processed", "failed")
        ]


class BandPeer:
    """Answers the agent API the runtime uses; records what it was asked."""

    def __init__(self, observations: Observations) -> None:
        self.observations = observations
        self.rooms: dict[str, PeerRoom] = {}
        self.listings = 0
        self.held_requests: list[str] = []
        self._ids = itertools.count(1)
        self._holds: dict[str, asyncio.Event] = {}

    def hold_room(self, room_id: str) -> None:
        """Hold every request about ``room_id`` until ``release_room``."""
        self._holds[room_id] = asyncio.Event()

    def release_room(self, room_id: str) -> None:
        self._holds.pop(room_id).set()

    def release_everything(self) -> None:
        for hold in self._holds.values():
            hold.set()
        self._holds.clear()

    def room(self, room_id: str) -> PeerRoom:
        return self.rooms.setdefault(room_id, PeerRoom())

    def post(self, room_id: str, content: str) -> dict[str, Any]:
        """Store a user message the agent must handle; returns its JSON."""
        message = message_json(room_id, f"m{next(self._ids)}", content)
        self.room(room_id).messages.append(message)
        return message

    async def answer(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.removeprefix(API_PREFIX).split("/")
        if len(parts) > 1 and (hold := self._holds.get(parts[1])) is not None:
            self.held_requests.append("/".join(parts))
            self.observations.pulse()
            await hold.wait()
        response = self._route(request, parts)
        self.observations.pulse()
        return response

    def _route(self, request: httpx.Request, parts: list[str]) -> httpx.Response:
        match parts, request.method:
            case ["me"], _:
                return _data(
                    {
                        "id": AGENT_ID,
                        "name": "Agent",
                        "handle": "agent",
                        "owner_uuid": "user-1",
                        "feature_flags": {},
                        "inserted_at": STAMP,
                        "updated_at": STAMP,
                    }
                )
            case ["chats"], "GET":
                self.listings += 1
                members = [
                    room_json(r) for r, room in self.rooms.items() if room.member
                ]
                return _data(members, metadata={"total_pages": 1})
            case ["chats", room_id, "messages", "next"], _:
                return self._next(self.room(room_id))
            case ["chats", room_id, "messages", message_id, mark], "POST":
                return self._mark(room_id, self.room(room_id), message_id, mark)
            case ["chats", room_id, "messages"], "GET":
                return self._listing(self.room(room_id), request)
            case _:
                return _data([], metadata={"has_more": False, "limit": 50})

    def _next(self, room: PeerRoom) -> httpx.Response:
        room.next_reads += 1
        outstanding = room.outstanding()
        if room.stopped or not outstanding:
            return httpx.Response(204)
        return _data(outstanding[0])

    def _mark(
        self, room_id: str, room: PeerRoom, message_id: str, mark: str
    ) -> httpx.Response:
        if room.stopped:
            return httpx.Response(204)
        room.status[message_id] = mark
        self.observations.record(f"{room_id}: {mark} {message_id}")
        return _data(
            {"id": message_id, "attempt_number": 1, "status": mark, "success": True}
        )

    def _listing(self, room: PeerRoom, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("status") == "processing":
            claimed = [
                m for m in room.messages if room.status.get(m["id"]) == "processing"
            ]
            return _data(claimed, metadata={"total_pages": 1})
        outstanding = room.outstanding()
        return _data(
            outstanding,
            metadata={
                "limit": len(outstanding),
                "has_more": False,
                "next_cursor": None,
            },
        )


def _data(data: Any, *, metadata: dict[str, Any] | None = None) -> httpx.Response:
    body: dict[str, Any] = {"data": data}
    if metadata is not None:
        body["metadata"] = metadata
    return httpx.Response(200, json=body)
