"""A started Codex adapter over a scripted app-server, driven as the runtime drives it."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from band.adapters.codex import CodexAdapter
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.integrations.codex import RpcEvent
from band.integrations.codex.types import CodexRequestMethod
from band.testing import FakeAgentTools
from tests.adapters.codex.kit import approval_tokens_from_chat
from tests.adapters.codexturns import (
    FakeCodexClient,
    await_released_turn,
    event_request,
    tool_call_request,
)
from tests.adapters.dispatch import deliver_event
from tests.adapters.waiting import until

ROOM_ID = "room-1"
ALICE = {"id": "alice", "name": "Alice"}
HELPER = {"id": "helper", "name": "Helper"}
CAROL = {"id": "carol", "name": "Carol"}
ADMIN = {"id": "admin-1", "name": "Admin"}
ROOM_MEMBERS = [
    {**sender, "role": "member", "status": "active", "type": "User"}
    for sender in (ALICE, HELPER, CAROL, ADMIN)
]


def approval_request(request_id: int) -> RpcEvent:
    """Codex asking the room to approve one command; the room is told its id."""
    return event_request(
        request_id, "item/commandExecution/requestApproval", {"command": "ls"}
    )


def reply_to_room() -> RpcEvent:
    """Codex answering the room through the Band reply tool."""
    return tool_call_request(
        1, "band_send_message", {"content": "Done.", "mentions": ["@alice"]}
    )


def room_message(
    content: str, *, message_id: str, sender: dict[str, str] = ALICE
) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id=ROOM_ID,
        content=content,
        sender_id=sender["id"],
        sender_type="User",
        sender_name=sender["name"],
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


class CodexBatchRoom:
    """One Codex room: people post messages, the scripted app-server answers,
    and the test reads what the room and the server saw."""

    def __init__(self, adapter: CodexAdapter, client: FakeCodexClient) -> None:
        self.adapter = adapter
        self.client = client
        self.deliveries: list[FakeAgentTools] = []
        self._message_ids = itertools.count(1)

    def message(
        self, content: str, *, sender: dict[str, str] = ALICE
    ) -> PlatformMessage:
        """The next message ``sender`` posts to the room."""
        return room_message(
            content, message_id=f"msg-{next(self._message_ids)}", sender=sender
        )

    def alice_helper_alice(self) -> tuple[PlatformMessage, ...]:
        """The queued originals of one batch: Alice, Helper, then Alice again."""
        return (
            self.message("list the files", sender=ALICE),
            self.message("fyi: the build is green", sender=HELPER),
            self.message("then jot a note", sender=ALICE),
        )

    def _turn_input(self, messages: tuple[PlatformMessage, ...]) -> AgentInput:
        self.deliveries.append(
            tools := FakeAgentTools(room_id=ROOM_ID, participants=ROOM_MEMBERS)
        )
        return AgentInput(
            msg=messages[0],
            messages=messages,
            tools=tools,
            history=HistoryProvider(raw=[]),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=len(self.deliveries) == 1,
            room_id=ROOM_ID,
        )

    async def send(self, content: str, *, sender: dict[str, str] = ALICE) -> None:
        """Deliver one message through the runtime's singleton entry point."""
        await deliver_event(
            self.adapter, self._turn_input((self.message(content, sender=sender),))
        )

    async def send_batch(self, *messages: PlatformMessage) -> FakeAgentTools:
        """Deliver several originals as one turn; returns the turn's tools."""
        inp = self._turn_input(messages)
        await self.adapter.on_messages(inp)
        return inp.tools

    async def control(
        self, content: str, *, sender: dict[str, str] = ADMIN
    ) -> FakeAgentTools:
        """Deliver a room command as its own control; returns its tools."""
        tools = FakeAgentTools(room_id=ROOM_ID, participants=ROOM_MEMBERS)
        await self.adapter.try_handle_control(
            self.message(content, sender=sender), tools
        )
        return tools

    @property
    def chat(self) -> list[str]:
        return [m["content"] for tools in self.deliveries for m in tools.messages_sent]

    def mentions(self, fragment: str) -> list[list[str]]:
        """Whom each room message containing ``fragment`` mentions, in order."""
        return [
            [mention["id"] for mention in message["mentions"]]
            for tools in self.deliveries
            for message in tools.messages_sent
            if fragment in message["content"]
        ]

    async def approval_token(self, nth: int = 1) -> str:
        """The id the room's ``nth`` approval prompt advertised, once posted."""
        await until(
            lambda: len(approval_tokens_from_chat(self.chat)) >= nth,
            failure=f"The room never posted approval prompt {nth}",
        )
        return approval_tokens_from_chat(self.chat)[nth - 1]

    @property
    def turn_inputs(self) -> list[str]:
        """The text each ``turn/start`` sent the app-server, in order."""
        return [
            "\n".join(item["text"] for item in params["input"])
            for params in self.client.params_of(CodexRequestMethod.TURN_START)
        ]

    @property
    def decisions(self) -> list[tuple[int | str, str]]:
        """What the app-server was told for each approval request, in order."""
        return [
            (request_id, result["decision"])
            for request_id, result in self.client.responses
            if "decision" in result
        ]

    async def pending_approvals(self) -> str:
        """What ``/approvals`` answers for the room right now."""
        return (await self.control("/approvals")).messages_sent[-1]["content"]

    @property
    def turn(self) -> asyncio.Task[None]:
        return self.adapter._turn_tasks[ROOM_ID].task

    async def settled(self) -> None:
        """Wait for a turn a human decision released to finish."""
        await await_released_turn(self.adapter, ROOM_ID)


OpenRoom = Callable[..., Awaitable[CodexBatchRoom]]
