"""ClaudeSessionManager: one owned CLI session per room, through the real SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    CLIConnectionError,
    CLIJSONDecodeError,
    ResultMessage,
    ThinkingBlock,
)
from claude_agent_sdk.types import (
    CanUseTool,
    PermissionResult,
    PermissionResultAllow,
    ToolPermissionContext,
)

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import ReleaseCause
from band.integrations.claude_sdk import session_manager, transport
from band.integrations.claude_sdk.session_manager import (
    ClaudeRoomSession,
    ClaudeSessionManager,
    ClaudeSessionManagerStoppedError,
)
from tests.adapters.claude_sdk.fakecli import AfterResult, FakeClaude, Hold, Thinking
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, exchange
from tests.adapters.claude_sdk.process import WorkspacePeer

# A result for work the CLI finished after the turn that started it ended.
STRAY_RESULT = {
    "type": "result",
    "subtype": "success",
    "duration_ms": 1,
    "duration_api_ms": 1,
    "is_error": False,
    "num_turns": 1,
    "session_id": "background",
    "result": "background work finished",
}


@pytest.fixture
def claude() -> Iterator[FakeClaude]:
    claude = FakeClaude()
    with patch(
        "band.integrations.claude_sdk.session_manager.create_transport",
        claude.transport,
    ):
        yield claude
    claude.assert_done()


def thinking(text: str) -> list[Thinking]:
    """A turn that only thinks, so it needs no Band MCP server."""
    return [Thinking(text)]


def thoughts(messages: list[Any]) -> list[str]:
    return [
        block.thinking
        for message in messages
        for block in getattr(message, "content", [])
        if isinstance(block, ThinkingBlock)
    ]


def new_manager(**options: Any) -> ClaudeSessionManager:
    return ClaudeSessionManager(
        ClaudeAgentOptions(model="claude-fake", mcp_servers={}),
        retention=REPORTING_BUDGET,
        **options,
    )


class TaskRecordingPeer(WorkspacePeer):
    """A real CLI child that notes which task drives each transport call."""

    tasks: dict[str, asyncio.Task[Any] | None]

    async def connect(self) -> None:
        self.tasks = {"connect": asyncio.current_task()}
        await super().connect()

    async def write(self, data: str) -> None:
        if json.loads(data)["type"] == "user":
            self.tasks["query"] = asyncio.current_task()
        await super().write(data)

    async def close(self) -> None:
        self.tasks.setdefault("close", asyncio.current_task())
        await super().close()


def allow_everything(session: ClaudeRoomSession, generation: int) -> CanUseTool:
    async def decide(
        tool_name: str, arguments: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        return PermissionResultAllow(updated_input=arguments)

    return decide


async def test_the_installed_sdk_runs_one_session_across_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connect, query and close each run in a different task on one loop,
    against a real child process: the SDK floor needs no task-affine actor."""
    children: list[TaskRecordingPeer] = []

    def launch(*, prompt: str, options: ClaudeAgentOptions) -> TaskRecordingPeer:
        child = TaskRecordingPeer(prompt=prompt, options=options)
        children.append(child)
        return child

    monkeypatch.setattr(transport, "SubprocessCLITransport", launch)
    manager = ClaudeSessionManager(
        ClaudeAgentOptions(cli_path=sys.executable, cwd=tmp_path),
        can_use_tool_factory=allow_everything,
    )
    session = manager.room("room-a")
    request = json.dumps(
        {"tool_name": "Write", "input": {"file_path": "notes.txt", "content": "x"}}
    )
    try:
        async with session.using() as client:
            with session.listening() as stream:
                await asyncio.create_task(client.query(request))
                [*_, result] = [message async for message in stream.until_result()]
        await asyncio.create_task(manager.cleanup_session("room-a"))
    finally:
        await manager.stop()

    [child] = children
    assert isinstance(result, ResultMessage)
    assert (result.is_error, result.result) == (False, "x")
    assert child.exited
    assert len({*child.tasks.values(), asyncio.current_task()}) == 4


async def test_a_held_connect_or_close_never_holds_up_another_room(
    claude: FakeClaude,
) -> None:
    manager = new_manager()
    claude.script(thinking("b first"), thinking("a"), thinking("b again"))
    connecting = claude.connecting = Hold()
    opening = asyncio.create_task(exchange(manager.room("room-a"), "hi"))
    async with connecting:
        assert thoughts(await exchange(manager.room("room-b"), "hi")) == ["b first"]
    await opening

    closing = claude.closing = Hold()
    leaving = asyncio.create_task(manager.cleanup_session("room-a"))
    async with closing:
        assert thoughts(await exchange(manager.room("room-b"), "hi")) == ["b again"]
    await leaving
    await manager.stop()

    assert [session.alive for session in claude.sessions] == [False, False]


class Signal(logging.Handler):
    """Sets ``seen`` once a record containing ``fragment`` is logged."""

    def __init__(self, fragment: str) -> None:
        super().__init__(logging.DEBUG)
        self.fragment = fragment
        self.seen = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if self.fragment in record.getMessage():
            self.seen.set()


async def test_one_reader_keeps_output_between_turns_out_of_the_next(
    claude: FakeClaude, caplog: pytest.LogCaptureFixture
) -> None:
    """The CLI reports background work after a turn's result. The room's
    single reader drops what arrives while no turn listens, so it is never
    buffered for the next turn, which ends at its own result."""
    manager = new_manager()
    session = manager.room("room-a")
    finished = asyncio.Event()
    claude.script([AfterResult(STRAY_RESULT, gate=finished)], thinking("second"))
    dropped = Signal("outside any turn")
    session_logger = logging.getLogger(session_manager.__name__)
    session_logger.addHandler(dropped)
    try:
        with caplog.at_level(logging.DEBUG, logger=session_manager.__name__):
            await exchange(session, "first")
            finished.set()
            await dropped.seen.wait()
            second = await exchange(session, "second")
    finally:
        session_logger.removeHandler(dropped)
        await manager.stop()

    assert thoughts(second) == ["second"]
    assert [m.session_id for m in second if isinstance(m, ResultMessage)] == ["sess-1"]
    assert len(claude.sessions) == 1


@pytest.mark.parametrize(
    ("resume", "attempts", "kept"),
    [(True, ["sess-gone"], "sess-gone"), (False, ["sess-gone", None], None)],
    ids=["strict-wake", "bootstrap-fallback"],
)
async def test_only_a_strict_wake_keeps_an_unresumable_session(
    claude: FakeClaude, resume: bool, attempts: list[str | None], kept: str | None
) -> None:
    """A deliberate wake reports the failure and keeps the conversation's
    identity; bootstrap and crash recovery start a new conversation."""
    manager = new_manager()
    session = manager.room("room-a")
    session.adopt("sess-gone")
    claude.unresumable.add("sess-gone")

    if resume:
        with pytest.raises(CLIConnectionError):
            await session.acquire(1, resume)
    else:
        await session.acquire(1, resume)
    await session.release(ReleaseCause.FINAL)

    assert claude.resumed == attempts
    assert session.session_id == kept
    assert not any(cli.alive for cli in claude.sessions)


async def test_a_retired_or_replaced_residency_loses_authority(
    claude: FakeClaude,
) -> None:
    manager = new_manager()
    claude.script(thinking("one"), thinking("two"), thinking("three"))
    first = manager.room("room-a")
    await exchange(first, "one")
    assert first.speaks_for(1)

    assert first.recover("crashed")
    assert not first.speaks_for(1)
    await exchange(first, "two")
    assert first.speaks_for(2)

    await manager.cleanup_session("room-a")
    rejoined = manager.room("room-a")
    await exchange(rejoined, "three")
    # The rejoined room counts generations from the start again.
    authority = [first.speaks_for(1), rejoined.speaks_for(1)]
    await manager.stop()

    assert rejoined is not first
    assert authority == [False, True]
    assert len(claude.sessions) == 3


async def test_failed_startup_cleanup_is_retried_on_the_same_process(
    claude: FakeClaude,
) -> None:
    manager = new_manager()
    session = manager.room("room-a")
    claude.refuse_connect = claude.refuse_close = True

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await exchange(session, "hi")
    claude.refuse_connect = False
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await exchange(session, "hi")
    assert manager.has_session("room-a")
    assert len(claude.sessions) == 1

    claude.refuse_close = False
    claude.script(thinking("started"))
    assert thoughts(await exchange(session, "hi")) == ["started"]
    await manager.stop()

    assert [cli.alive for cli in claude.sessions] == [False, False]


async def test_a_cli_that_exits_between_turns_is_retired_by_its_reader(
    claude: FakeClaude,
) -> None:
    """No turn is listening when the output ends, so the reader itself hands
    the crashed CLI to the room's owner, which closes it; the next message
    starts a new conversation, as a crash noticed inside a turn does."""
    manager = new_manager()
    session = manager.room("room-a")
    session.adopt("sess-1")
    claude.script(thinking("hi"), thinking("again"))
    await exchange(session, "hi")
    closing = claude.closing = Hold()

    crashed, *_ = claude.sessions
    crashed.die()
    async with closing:
        retired = session.speaks_for(1)
    await exchange(session, "again")
    await manager.stop()

    assert retired is False
    assert claude.resumed == ["sess-1", None]


async def test_a_read_failure_after_a_result_retires_the_cli(
    claude: FakeClaude,
) -> None:
    """The SDK ends its message stream on a malformed line while the CLI runs
    on. The turn already has its result and reads no further, so the reader
    retires the CLI instead of leaving the next turn on a stream nobody reads."""
    manager = new_manager()
    session = manager.room("room-a")
    malformed = asyncio.Event()
    unreadable = CLIJSONDecodeError("{", ValueError("Buffer size exceeds limit"))
    claude.script(
        [*thinking("one"), AfterResult(unreadable, gate=malformed)], thinking("two")
    )
    retired = Signal("retiring its CLI")
    session_logger = logging.getLogger(session_manager.__name__)
    session_logger.addHandler(retired)
    try:
        async with session.using() as client:
            with session.listening() as stream:
                await client.query("one")
                [_ async for _ in stream.until_result()]
                malformed.set()
                await retired.seen.wait()
        second = await exchange(session, "two")
    finally:
        session_logger.removeHandler(retired)
        await manager.stop()

    assert thoughts(second) == ["two"]
    assert len(claude.sessions) == 2


async def test_a_startup_that_fails_twice_keeps_the_conversation(
    claude: FakeClaude,
) -> None:
    """The fresh-session fallback drops the conversation only once a new one
    runs, so a startup failure unrelated to the resume leaves the next
    message resuming it."""
    manager = new_manager()
    session = manager.room("room-a")
    session.adopt("sess-1")
    claude.refuse_connect = True

    with pytest.raises(CLIConnectionError):
        await exchange(session, "hi")
    claude.refuse_connect = False
    claude.script(thinking("back"))
    await exchange(session, "hi")
    await manager.stop()

    assert claude.resumed == ["sess-1", None, "sess-1"]


async def test_a_failed_resume_that_could_not_close_is_never_replaced(
    claude: FakeClaude,
) -> None:
    """The bootstrap fallback only starts a new CLI once the failed one is
    closed; otherwise the failed CLI stays held for recovery."""
    manager = new_manager()
    session = manager.room("room-a")
    session.adopt("sess-gone")
    claude.unresumable.add("sess-gone")
    claude.refuse_close = True

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await exchange(session, "hi")
    [cli] = claude.sessions
    held = (manager.has_session("room-a"), session.session_id)
    claude.refuse_close = False
    await manager.cleanup_session("room-a")

    assert held == (True, "sess-gone")
    assert claude.resumed == ["sess-gone"]
    assert not cli.alive


async def test_an_unfinished_disposal_stays_owned_and_a_retry_joins_it(
    claude: FakeClaude,
) -> None:
    manager = new_manager()
    session = manager.room("room-a")
    claude.script(thinking("hi"))
    await exchange(session, "hi")
    closing = claude.closing = Hold()

    leaving = asyncio.create_task(manager.cleanup_session("room-a"))
    await closing.reached.wait()
    leaving.cancel()
    with pytest.raises(CleanupIncompleteError):
        await manager.cleanup_session("room-a")
    [cli] = claude.sessions
    assert (cli.close_calls, manager.has_session("room-a")) == (1, True)

    closing.released.set()
    await manager.cleanup_session("room-a")
    await manager.stop()

    assert not cli.alive
    assert manager.session("room-a") is None


async def test_a_stopped_manager_keeps_failed_rooms_and_refuses_new_ones(
    claude: FakeClaude,
) -> None:
    manager = new_manager()
    claude.script(thinking("hi"))
    await exchange(manager.room("room-a"), "hi")
    claude.refuse_close = True

    with pytest.raises(ExceptionGroup) as failed:
        await manager.stop()
    assert failed.group_contains(RuntimeError, match="cleanup failed")
    with pytest.raises(ClaudeSessionManagerStoppedError):
        manager.room("room-b")
    claude.refuse_close = False
    await manager.stop()

    [cli] = claude.sessions
    assert not cli.alive
    assert manager.get_active_rooms() == []


async def test_room_options_bind_the_room_its_workspace_and_its_residency(
    claude: FakeClaude, tmp_path: Path
) -> None:
    bindings: list[tuple[str, int]] = []

    def bind(session: ClaudeRoomSession, generation: int) -> CanUseTool:
        bindings.append((session.room_id, generation))
        return allow_everything(session, generation)

    manager = ClaudeSessionManager(
        ClaudeAgentOptions(model="claude-fake", mcp_servers={}),
        can_use_tool_factory=bind,
        mcp_servers_factory=lambda room_id: {"band": {"type": "http", "url": room_id}},
    )
    base = manager.base_options
    claude.script(thinking("hi"))
    session = manager.room("room-a")
    session.adopt("sess-earlier")
    await exchange(session, "hi")
    await manager.stop()

    [cli] = claude.sessions
    assert cli.options.resume == "sess-earlier"
    assert cli.options.mcp_servers == {"band": {"type": "http", "url": "room-a"}}
    assert cli.options.model == base.model
    assert base.resume is None and base.mcp_servers == {}
    assert bindings == [("room-a", 1)]
