"""Backend-stop probes for the opencode adapter; see ``stopprobes``."""

from __future__ import annotations

import asyncio

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.client.streaming import ControlMode
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_message_updated,
    event_permission,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
    wait_for,
)
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_room,
)

ROOM_ID = "room-1"
# The fake server numbers its first session.
SESSION_ID = "sess-1"
ABORT_FIRST_SESSION = f"abort {SESSION_ID}"
CLIENT_CLOSED = "client closed"
# What the server streams once an aborted turn winds down; a watcher still
# running would post it as the turn's fallback reply.
LATE_TEXT = "text from the stopped turn"


class JournalingOpencodeClient(FakeOpencodeClient):
    """Records session aborts and its own close, in the order they happen."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.journal: list[str] = []

    async def abort_session(self, session_id: str) -> None:
        await super().abort_session(session_id)
        self.journal.append(f"abort {session_id}")

    async def close(self) -> None:
        self.journal.append(CLIENT_CLOSED)
        await super().close()


async def started(
    client: JournalingOpencodeClient, config: OpencodeAdapterConfig | None = None
) -> OpencodeAdapter:
    adapter = OpencodeAdapter(
        config=config or OpencodeAdapterConfig(), client_factory=lambda _: client
    )
    await adapter.on_started("OpenCode Agent", "A coding agent")
    return adapter


def deliver(adapter: OpencodeAdapter, tools: FakeAgentTools) -> asyncio.Task[None]:
    return asyncio.create_task(
        deliver_message(
            adapter,
            make_platform_message(content="long task"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=ROOM_ID,
        )
    )


async def stopped_turn_winds_down(client: JournalingOpencodeClient) -> None:
    """The server streams the stopped turn's last text and goes idle."""
    for event in (
        event_message_updated(SESSION_ID, "msg-late"),
        event_text_part(SESSION_ID, "msg-late", LATE_TEXT),
        event_session_idle(SESSION_ID),
    ):
        await client.push_event(event)
    for _ in range(10):
        await asyncio.sleep(0)


def posted(tools: FakeAgentTools, before: int) -> list[str]:
    return [f"posted {message['content']}" for message in tools.messages_sent[before:]]


async def end_running_turn(mode: ControlMode) -> list[str]:
    client = JournalingOpencodeClient(prompt_event_sequences=[[]])
    adapter = await started(client)
    tools = FakeAgentTools()
    turn = deliver(adapter, tools)
    await wait_for(lambda: bool(client.prompt_calls))
    await end_turn(adapter, turn, ROOM_ID, mode)
    await stopped_turn_winds_down(client)
    recorded = client.journal + posted(tools, 0)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


async def stop_detached_after_approval() -> list[str]:
    """A manual ask releases on_message; a later STOP still stops the turn."""
    client = JournalingOpencodeClient(
        prompt_event_sequences=[[event_permission(SESSION_ID, "perm-1")]]
    )
    adapter = await started(client, OpencodeAdapterConfig(approval_mode="manual"))
    tools = FakeAgentTools()
    await deliver(adapter, tools)
    asked = len(tools.messages_sent)
    await stop_room(adapter, ROOM_ID)
    await stopped_turn_winds_down(client)
    recorded = client.journal + posted(tools, asked)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


async def cleanup_after_stop() -> list[str]:
    """Teardown right after STOP aborts the session before closing the client."""
    client = JournalingOpencodeClient(prompt_event_sequences=[[]])
    adapter = await started(client)
    turn = deliver(adapter, FakeAgentTools())
    await wait_for(lambda: bool(client.prompt_calls))
    turn.cancel()
    await asyncio.gather(turn, return_exceptions=True)
    await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
    await adapter.on_cleanup(ROOM_ID)
    return client.journal


async def idle() -> list[str]:
    client = JournalingOpencodeClient()
    adapter = await started(client)
    await stop_room(adapter, ROOM_ID)
    recorded = list(client.journal)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=[ABORT_FIRST_SESSION],
        )
        for mode in ENDING_CONTROLS
    },
    "stop-detached-after-approval": StopCase(
        run=stop_detached_after_approval, expected=[ABORT_FIRST_SESSION]
    ),
    "cleanup-after-stop": StopCase(
        run=cleanup_after_stop, expected=[ABORT_FIRST_SESSION, CLIENT_CLOSED]
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
