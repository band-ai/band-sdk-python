"""Explicit room membership shared by the originating-audience adapter peers."""

from __future__ import annotations

from typing import Any

ALICE = {
    "id": "user-1",
    "name": "Alice",
    "type": "User",
    "role": "member",
    "status": "active",
    "handle": "alice",
}
BOB = {
    "id": "user-2",
    "name": "Bob",
    "type": "User",
    "role": "member",
    "status": "active",
    "handle": "bob",
}
AGENT = {
    "id": "sdk-agent",
    "name": "SDK Agent",
    "type": "Agent",
    "role": "member",
    "status": "active",
}
HELPER = {
    "id": "agent-7",
    "name": "Helper",
    "type": "Agent",
    "role": "member",
    "status": "active",
}
ROOM_MEMBERS: list[dict[str, Any]] = [ALICE, BOB, HELPER, AGENT]
