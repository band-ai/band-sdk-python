"""Stateful platform peer for the standalone door: it keeps the rooms,
participants and messages it is given, answers the agent and human REST
routes the door's tools reach, and refuses every other route."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

import httpx

from band.core.exceptions import MessagePostKind

TIMESTAMP = "2026-01-01T00:00:00Z"


@dataclass(frozen=True)
class Identity:
    id: str
    name: str
    handle: str
    type: str

    def as_peer(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "handle": self.handle,
            "type": self.type,
            "is_contact": False,
            "online": True,
            "source": "registry",
        }

    def as_participant(self, role: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "handle": self.handle,
            "type": self.type,
            "role": role,
            "status": "active",
        }


AGENT = Identity(str(uuid.UUID(int=1)), "Scout", "owner/scout", "Agent")
OWNER = Identity(str(uuid.UUID(int=2)), "Olive Owner", "olive", "User")
COLLEAGUE = Identity(str(uuid.UUID(int=3)), "Reviewer", "owner/reviewer", "Agent")
DIRECTORY = (AGENT, OWNER, COLLEAGUE)
OWNER_EMAIL = "olive@band.test"
AGENT_KEY = "agent-key"
USER_KEY = "user-key"
# Each REST surface answers only its own credential.
SURFACE_KEYS = {"agent": AGENT_KEY, "me": USER_KEY}


def room_record(room_id: str) -> dict[str, Any]:
    """A room as every chat route returns it."""
    return {"id": room_id, "inserted_at": TIMESTAMP, "updated_at": TIMESTAMP}


@dataclass
class Room:
    id: str
    participants: list[dict[str, Any]]
    messages: list[dict[str, Any]] = field(default_factory=list)

    def as_wire(self) -> dict[str, Any]:
        return room_record(self.id)

    def has_member(self, participant_id: str) -> bool:
        return any(p["id"] == participant_id for p in self.participants)


def data(body: Any, status: HTTPStatus = HTTPStatus.OK) -> httpx.Response:
    return httpx.Response(status, json={"data": body})


def error(status: HTTPStatus, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": status.phrase.lower().replace(" ", "_"),
                "message": message,
                "request_id": "req-1",
            }
        },
    )


class Platform:
    """The Band platform as the door's agent (``AGENT``) and its owner (``OWNER``) see it."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def join(self, room_id: str, who: Identity) -> None:
        """Add ``who`` to a room outside the door, as another member would."""
        self.rooms[room_id].participants.append(who.as_participant("member"))

    def answer(self, request: httpx.Request) -> httpx.Response:
        route = request.url.path.strip("/").split("/")
        if request.headers.get("x-api-key") != SURFACE_KEYS.get(route[2]):
            return error(HTTPStatus.UNAUTHORIZED, "Invalid API key")
        body = json.loads(request.content) if request.content else {}
        match (request.method, *route):
            case ("GET", "api", "v1", "agent", "me"):
                return data(self.agent_me())
            case ("POST", "api", "v1", "agent", "chats"):
                return data(self.open_room(AGENT).as_wire(), HTTPStatus.CREATED)
            case ("GET", "api", "v1", "agent", "peers"):
                return self.peers(request.url.params)
            case ("GET", "api", "v1", "agent", "chats", room_id, "participants"):
                return self.in_room(room_id, lambda room: data(room.participants))
            case ("POST", "api", "v1", "agent", "chats", room_id, "participants"):
                return self.in_room(
                    room_id, lambda room: self.add(room, body["participant"])
                )
            case ("POST", "api", "v1", "agent", "chats", room_id, "messages"):
                return self.in_room(room_id, lambda room: self.post(room, body))
            case ("GET", "api", "v1", "me", "profile"):
                return data(self.owner_profile())
            case ("GET", "api", "v1", "me", "chats"):
                return self.owner_chats()
            case ("POST", "api", "v1", "me", "chats"):
                return data(self.open_room(OWNER).as_wire(), HTTPStatus.CREATED)
            case ("GET", "api", "v1", "me", "chats", room_id):
                return self.in_room(room_id, lambda room: data(room.as_wire()))
        raise NotImplementedError(
            f"platform peer does not serve {request.method} {request.url.path}"
        )

    def agent_me(self) -> dict[str, Any]:
        return {
            "id": AGENT.id,
            "name": AGENT.name,
            "handle": AGENT.handle,
            "owner_uuid": OWNER.id,
            "feature_flags": {},
            "inserted_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
        }

    def owner_profile(self) -> dict[str, Any]:
        return {
            "id": OWNER.id,
            "handle": OWNER.handle,
            "email": OWNER_EMAIL,
            "first_name": "Olive",
            "last_name": "Owner",
            "role": "user",
        }

    def open_room(self, creator: Identity) -> Room:
        room = Room(
            str(uuid.UUID(int=100 + len(self.rooms))),
            [creator.as_participant("owner")],
        )
        self.rooms[room.id] = room
        return room

    def in_room(
        self, room_id: str, act: Callable[[Room], httpx.Response]
    ) -> httpx.Response:
        room = self.rooms.get(room_id)
        if room is None:
            return error(HTTPStatus.NOT_FOUND, "Chat room not found")
        return act(room)

    def peers(self, params: httpx.QueryParams) -> httpx.Response:
        page, page_size = int(params["page"]), int(params["page_size"])
        room = self.rooms[params["not_in_chat"]]
        outside = [
            who.as_peer()
            for who in DIRECTORY
            if who != AGENT and not room.has_member(who.id)
        ]
        start = (page - 1) * page_size
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "data": outside[start : start + page_size],
                "metadata": {
                    "page": page,
                    "page_size": page_size,
                    "total_count": len(outside),
                    "total_pages": max(1, -(-len(outside) // page_size)),
                },
            },
        )

    def add(self, room: Room, request: dict[str, Any]) -> httpx.Response:
        who = next((i for i in DIRECTORY if i.id == request["participant_id"]), None)
        if who is None:
            return error(HTTPStatus.NOT_FOUND, "Participant not found")
        participant = who.as_participant(request.get("role", "member"))
        room.participants.append(participant)
        return data(participant, HTTPStatus.CREATED)

    def post(self, room: Room, request: dict[str, Any]) -> httpx.Response:
        message = request["message"]
        mentioned = [m["id"] for m in message["mentions"]]
        if not all(room.has_member(m) for m in mentioned):
            return error(
                HTTPStatus.UNPROCESSABLE_ENTITY, MessagePostKind.MENTION_NOT_IN_ROOM
            )
        message_id = str(uuid.UUID(int=1000 + len(room.messages)))
        room.messages.append({"id": message_id, **message})
        recipients = [
            {"id": p["id"], "handle": p["handle"], "name": p["name"]}
            for p in room.participants
            if p["id"] in mentioned
        ]
        return data(
            {
                "id": message_id,
                "recipients": recipients,
                "success": True,
            },
            HTTPStatus.CREATED,
        )

    def owner_chats(self) -> httpx.Response:
        rooms = [r.as_wire() for r in self.rooms.values() if r.has_member(OWNER.id)]
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "data": rooms,
                "metadata": {
                    "page": 1,
                    "page_size": len(rooms),
                    "total_count": len(rooms),
                    "total_pages": 1,
                },
            },
        )
