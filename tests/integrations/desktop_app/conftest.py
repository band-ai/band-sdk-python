"""The room the Desktop view tests work in, and the ways they drive it."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any

import pytest
from mcp import types

from band.integrations.desktop_app.event_relay import RelayStatus, RoomEventBroker
from band.integrations.desktop_app.server import create_server
from band.integrations.desktop_app.service import RoomTranscriptService
from band.integrations.desktop_app.settings import RoomViewTuning
from band.integrations.desktop_app.tools import RoomTool

ROOM_ID = "room-1"
TOM = {
    "id": "agent-1",
    "name": "tom",
    "handle": "alexander.zaikman/tom",
    "description": "Tom the cat",
}
ROSTER = [
    {
        "id": "human-1",
        "name": "Alexander Zaikman",
        "handle": "alexander.zaikman",
        "type": "User",
        "role": "owner",
    },
    {
        "id": "jerry-1",
        "name": "jery",
        "handle": "alexander.zaikman/jery",
        "type": "Agent",
        "role": "member",
    },
    {"id": TOM["id"], "name": TOM["name"], "type": "Agent", "role": "member"},
]
# Two titles share a word so name resolution has something ambiguous to find.
ROOMS = [
    {"id": ROOM_ID, "title": "Project Alpha"},
    {"id": "room-2", "title": "Alpha retrospective"},
    {"id": "room-3", "title": "Watercooler"},
]

# A follower has no socket of its own to lose, so it is always live: the
# cheapest way to say "the WebSocket is up" in a test.
LIVE = RelayStatus(role="follower")
DEAD = RelayStatus(role="leader", websocket_connected=False)


def message(message_id: str, inserted_at: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "content": message_id,
        "inserted_at": inserted_at,
        "message_type": "text",
        "sender_id": "sender-1",
        "sender_name": "Tom",
        "sender_type": "Agent",
    }


def mentioned_message(
    message_id: str,
    inserted_at: str,
    *,
    sender_id: str = "jerry-1",
    sender_name: str = "Jerry",
) -> dict[str, Any]:
    """A message addressing the connected agent, stored as Band stores it."""
    return {
        **message(message_id, inserted_at),
        "content": f"@[[{TOM['id']}]] {message_id}",
        "sender_id": sender_id,
        "sender_name": sender_name,
        "metadata": {"mentions": [{"id": TOM["id"]}]},
    }


def ids(messages: list[Any]) -> list[str]:
    """Message ids, whether the caller holds models or serialized payloads."""
    return [
        item["id"] if isinstance(item, dict) else item.id  # noqa: SIM401
        for item in messages
    ]


class FakeTranscriptTools:
    """The agent-scoped REST reads, paginating a scripted room oldest first.

    Each entry in ``states`` is the room's whole agent-visible history for one
    read, so a test says what the room holds on the first read, the second, and
    so on. Reads past the script keep seeing the last state described.
    """

    def __init__(self, states: list[list[dict[str, Any]]]) -> None:
        self.states = states
        self.reads = 0
        self.calls: list[dict[str, Any]] = []
        self.participant_calls = 0
        self.created_task_ids: list[str | None] = []
        self.profile_calls = 0

    async def get_agent_profile(self) -> dict[str, Any]:
        self.profile_calls += 1
        return TOM

    async def create_room(self, task_id: str | None = None) -> str:
        self.created_task_ids.append(task_id)
        return "created-room"

    async def list_rooms(self) -> list[dict[str, Any]]:
        return ROOMS

    async def list_participants(self, chat_id: str) -> list[dict[str, Any]]:
        self.participant_calls += 1
        return ROSTER

    async def list_agent_context(
        self,
        chat_id: str,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # Every read opens at page 1, which is what moves the script along.
        if page == 1:
            self.reads += 1
        self.calls.append({"chat_id": chat_id, "page": page, "page_size": page_size})
        history = self.states[min(self.reads, len(self.states)) - 1]
        start = (page - 1) * page_size
        return history[start : start + page_size], {
            "total_pages": max(1, ceil(len(history) / page_size)),
        }


def transcript(*reads: Any) -> FakeTranscriptTools:
    """Tools answering each successive read with the room state it describes."""
    return FakeTranscriptTools([list(read) for read in reads])


@dataclass
class Room:
    """A scripted room, offered at both surfaces the code is used through.

    ``read``/``wait`` exercise the service directly; ``join``/``monitor`` go
    the whole way round through the MCP server, the way Claude reaches it.
    """

    service: RoomTranscriptService
    tools: FakeTranscriptTools
    events: RoomEventBroker
    server: Any = field(init=False)

    def __post_init__(self) -> None:
        self.server = create_server(self.service)

    @property
    def rest_calls(self) -> int:
        """How many context API calls the room has cost so far."""
        return len(self.tools.calls)

    @property
    def roster_reads(self) -> int:
        return self.tools.participant_calls

    @property
    def tuning(self) -> RoomViewTuning:
        """The tuning this room actually runs on, not a re-read of the env."""
        return self.service.tuning

    async def publish(self, chat_id: str = ROOM_ID) -> None:
        """A room event arriving over the platform WebSocket."""
        await self.events.publish(chat_id)

    async def read(self, chat_id: str = ROOM_ID, **kwargs: Any) -> Any:
        return await self.service.read(chat_id, **kwargs)

    async def wait(self, chat_id: str = ROOM_ID, **kwargs: Any) -> Any:
        kwargs.setdefault("since", None)
        kwargs.setdefault("timeout_seconds", 1)
        return await self.service.wait_for_room_event(chat_id, **kwargs)

    async def call(self, tool: str, **arguments: Any) -> types.CallToolResult:
        """The raw tool result, for tests that care how a call failed."""
        result = await self.server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                params=types.CallToolRequestParams(name=tool, arguments=arguments)
            )
        )
        assert isinstance(result.root, types.CallToolResult)
        return result.root

    async def invoke(self, tool: str, **arguments: Any) -> dict[str, Any]:
        """A successful call's payload, plus the summary text the model reads."""
        result = await self.call(tool, **arguments)
        assert not result.isError, result.content
        assert result.structuredContent is not None
        summary = result.content[0]
        assert isinstance(summary, types.TextContent)
        return {**result.structuredContent, "summary_text": summary.text}

    async def join(self, chat_id: str = ROOM_ID) -> dict[str, Any]:
        return await self.invoke(RoomTool.JOIN, chat_id=chat_id)

    async def create(self, task_id: str | None = None) -> dict[str, Any]:
        return await self.invoke(RoomTool.CREATE, task_id=task_id)

    async def monitor(
        self,
        *,
        since: str | None = None,
        retry_wakes: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self.invoke(
            RoomTool.MONITOR,
            chat_id=ROOM_ID,
            since=since,
            timeout_seconds=1,
            retry_wakes=retry_wakes or [],
        )


@pytest.fixture
def room() -> Any:
    """Build a room whose REST reads answer the scripted pages, in order."""

    def build(
        *reads: Any,
        transport: RelayStatus | None = None,
        tuning: RoomViewTuning | None = None,
    ) -> Room:
        tools = transcript(*reads)
        events = RoomEventBroker()
        service = RoomTranscriptService(
            tools,
            events=events,
            transport=transport,
            tuning=tuning,
        )
        return Room(service, tools, events)

    return build
