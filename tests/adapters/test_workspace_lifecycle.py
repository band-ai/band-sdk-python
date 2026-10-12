"""One room ownership contract through both adapters and real children.

``RetentionHost`` drives an adapter the way the runtime does and projects
what a room owns: its native conversation, its children and their workspace.
Cells pinned to ``codex`` state the ownership contract only Codex implements
so far; the Claude adapter joins them when its rewrite lands.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError

from band.adapters.claude_sdk import (
    ClaudeApprovalOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
    CodexCommand,
    CodexContinuityError,
    CodexSubcommand,
)
from band.core.exceptions import InputNotAccepted
from band.core.protocols import TurnResultAlreadyReported
from band.core.retention import Residency
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.integrations.claude_sdk import transport
from band.integrations.codex import CodexJsonRpcError, CodexStdioClient
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.process import WorkspacePeer
from tests.adapters.roompeer import PeerCommand, memo_instruction, write_instruction
from tests.framework_conformance.turnprobes import DispatchingFakeTools
from tests.paths import REPO_ROOT

# A transition settles in a few loop turns; this bounds a wait that never would.
MAX_SETTLE_POLLS = 500
IN_FLIGHT = frozenset({Residency.ACQUIRING, Residency.SUSPENDING})
ROOM_REFUSES_WORK = "cleanup failed|is closing"


class CodexPeer(CodexStdioClient):
    """Inject a held start or a failed or held close at the real transport."""

    refuse_close = False
    starting: Hold | None = None
    closing: Hold | None = None

    async def initialize(self, **kwargs: Any) -> dict[str, Any]:
        if self.starting is not None:
            self.starting.reached.set()
            await self.starting.released.wait()
        return await super().initialize(**kwargs)

    async def _close_process(self) -> None:
        if self.closing is not None:
            self.closing.reached.set()
            await self.closing.released.wait()
        if self.refuse_close:
            raise RuntimeError("subprocess cleanup failed")
        await super()._close_process()

    @property
    def exited(self) -> bool:
        return self._proc is not None and self._proc.returncode is not None


class ClaudePeer(WorkspacePeer):
    closing: Hold | None = None

    async def close(self) -> None:
        if self.closing is not None:
            self.closing.reached.set()
            await self.closing.released.wait()
        await super().close()


@dataclass
class RetentionHost:
    adapter: CodexAdapter | ClaudeSDKAdapter
    root: Path
    fail_startup: Path
    hold_startup: Path
    shared: bool = True
    children: list[CodexPeer | ClaudePeer] = field(default_factory=list)
    # Children started while an earlier child in the same workspace still ran.
    overlaps: int = 0
    next_start: Hold | None = None

    def workspace(self, room_id: str) -> str:
        return str(self.root / ("shared" if self.shared else room_id))

    def spawned(self, child: CodexPeer | ClaudePeer, cwd: str | None) -> None:
        self.overlaps += any(
            not earlier.exited and getattr(earlier, "cwd", None) == cwd
            for earlier in self.children
        )
        self.children.append(child)

    async def send(self, room_id: str, content: str) -> list[str]:
        tools = DispatchingFakeTools(room_id=room_id)
        await self.adapter.on_event(
            AgentInput(
                msg=PlatformMessage(
                    id=content,
                    room_id=room_id,
                    content=content,
                    sender_id="user",
                    sender_type="User",
                    sender_name="Alice",
                    message_type="text",
                    metadata={},
                    created_at=datetime.now(UTC),
                ),
                tools=tools,
                history=HistoryProvider(raw=[]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id=room_id,
            )
        )
        return [message["content"] for message in tools.messages_sent]

    async def leave(self, room_id: str) -> None:
        await self.adapter.on_cleanup(room_id)

    async def shutdown(self) -> None:
        await self.adapter.cleanup_all()

    async def settled(self, room_id: str) -> None:
        """Wait out the room's in-flight process transition, such as recovery."""
        if not isinstance(self.adapter, CodexAdapter):
            return
        if (room := self.adapter._room_clients.get(room_id)) is None:
            return
        for _ in range(MAX_SETTLE_POLLS):
            if room.residency.snapshot.state not in IN_FLIGHT:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Room {room_id} never settled")

    def native_id(self, room_id: str) -> str | None:
        assert isinstance(self.adapter, CodexAdapter), "Codex projection only"
        return self.adapter._room_threads.get(room_id)

    @property
    def exited(self) -> list[bool]:
        return [child.exited for child in self.children]


@pytest_asyncio.fixture(params=["codex", "claude"], loop_scope="function")
async def host(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    failure = tmp_path / "fail-startup"
    hold = tmp_path / "hold-startup"
    arguments = ("--fail-startup", str(failure), "--hold-startup", str(hold))
    if request.param == "codex":
        adapter = CodexAdapter(
            CodexAdapterConfig(
                model="peer",
                workspace_for_room=lambda room: host.workspace(room),
                codex_command=(
                    sys.executable,
                    "-u",
                    str(REPO_ROOT / "tests/adapters/roompeer.py"),
                    *arguments,
                    "--home",
                    str(tmp_path / "codex-home"),
                    "--stay",
                ),
            )
        )

        def codex_child(
            *,
            command: Sequence[str] | None,
            cwd: str | None,
            env: Mapping[str, str] | None,
        ) -> CodexPeer:
            child = CodexPeer(command=command, cwd=cwd, env=env)
            child.starting, host.next_start = host.next_start, None
            host.spawned(child, cwd)
            return child

        monkeypatch.setattr("band.adapters.codex.CodexStdioClient", codex_child)
    else:
        adapter = ClaudeSDKAdapter(
            ClaudeSDKAdapterConfig(
                approvals=ClaudeApprovalOptions(
                    mode="auto_accept", text_notifications=False
                )
            ),
            workspace_for_room=lambda room: host.workspace(room),
        )

        def claude_child(*, prompt: str, options: ClaudeAgentOptions) -> ClaudePeer:
            child = ClaudePeer(prompt=prompt, options=options)
            child.arguments = arguments
            host.spawned(child, None)
            return child

        monkeypatch.setattr(transport, "SubprocessCLITransport", claude_child)
    host = RetentionHost(adapter, tmp_path, failure, hold)
    await adapter.on_started("Peer", "A subprocess under test")
    try:
        yield host
    finally:
        failure.unlink(missing_ok=True)
        hold.unlink(missing_ok=True)
        for child in host.children:
            child.refuse_close = False
            for barrier in (getattr(child, "starting", None), child.closing):
                if barrier is not None:
                    barrier.released.set()
        try:
            await adapter.cleanup_all()
        finally:
            for child in host.children:
                await child.close()


async def test_completed_turns_retain_children_until_explicit_cleanup(
    host: RetentionHost,
) -> None:
    host.shared = False
    for room in ("room-a", "room-b", "room-c"):
        assert await host.send(room, write_instruction(room)) == [room]
    assert host.exited == [False, False, False]
    assert await host.send("room-a", PeerCommand.READ) == ["Read: room-a"]
    assert host.exited == [False, False, False]
    await host.shutdown()
    assert host.exited == [True, True, True]


async def test_failed_cleanup_retains_ownership_and_blocks_replacement(
    host: RetentionHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.children[0].refuse_close = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.leave("room-a")
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    with pytest.raises((RuntimeError, InputNotAccepted), match=ROOM_REFUSES_WORK):
        await host.send("room-a", write_instruction("changed"))
    assert host.exited == [False]
    assert (Path(host.workspace("room-a")) / "notes.txt").read_text() == "alpha"
    host.children[0].refuse_close = False
    await host.leave("room-a")
    assert host.exited == [True]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
    assert host.exited == [True, False]


async def test_failed_startup_keeps_membership_claim_until_leave(
    host: RetentionHost,
) -> None:
    host.fail_startup.touch()
    with pytest.raises(Exception, match="startup failed"):
        await host.send("room-a", write_instruction("alpha"))
    await host.settled("room-a")
    assert host.exited == [True]
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    await host.leave("room-a")
    host.fail_startup.unlink()
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]


@pytest.mark.parametrize("host", ["claude"], indirect=True)
async def test_failed_room_cleanup_can_recover_in_the_same_workspace(
    host: RetentionHost,
) -> None:
    """Pre-ownership behavior; superseded by the Codex cell below."""
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.children[0].refuse_close = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.leave("room-a")
    host.children[0].refuse_close = False
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
    assert host.exited == [True, False]
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))


@pytest.mark.parametrize("host", ["codex"], indirect=True)
async def test_a_failed_leave_is_final_until_retried_then_rejoin_starts_fresh(
    host: RetentionHost,
) -> None:
    assert await host.send("room-a", memo_instruction("kept")) == ["Noted."]
    left_thread = host.native_id("room-a")
    host.children[0].refuse_close = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.leave("room-a")
    host.children[0].refuse_close = False
    with pytest.raises(InputNotAccepted, match="is closing"):
        await host.send("room-a", PeerCommand.RECALL)
    assert host.native_id("room-a") == left_thread
    await host.leave("room-a")
    assert host.native_id("room-a") is None
    assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: None"]
    assert host.native_id("room-a") not in (None, left_thread)
    assert host.exited == [True, False]
    assert host.overlaps == 0


async def test_cancelled_startup_keeps_ownership_and_can_recover(
    host: RetentionHost,
) -> None:
    host.hold_startup.touch()
    opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
    while not (Path(host.workspace("room-a")) / "starting.txt").exists():
        await asyncio.sleep(0.01)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    host.hold_startup.unlink()
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    assert host.exited.count(False) == 1
    assert host.overlaps == 0
    await host.leave("room-a")
    assert all(host.exited)


async def test_cancelled_cleanup_can_be_completed_before_transfer(
    host: RetentionHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    barrier = host.children[0].closing = Hold()
    leaving = asyncio.create_task(host.leave("room-a"))
    async with barrier:
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving
        assert host.exited == [False]
    await host.leave("room-a")
    assert host.exited == [True]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]


async def test_shutdown_attempts_every_room_and_retains_failed_children(
    host: RetentionHost,
) -> None:
    host.shared = False
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
    host.children[0].refuse_close = True
    with pytest.raises(ExceptionGroup) as failed:
        await host.shutdown()
    assert failed.group_contains(RuntimeError, match="cleanup failed", depth=None)
    assert host.exited == [False, True]
    with pytest.raises(ExceptionGroup):
        await host.adapter.on_started("Peer", "Restarting after failed cleanup")
    assert host.exited == [False, True]
    host.children[0].refuse_close = False
    await host.adapter.on_started("Peer", "Restarting after successful cleanup")
    assert host.exited == [True, True]
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]


@pytest.mark.parametrize("loss", [PeerCommand.LOSE_TRANSPORT, PeerCommand.EXIT_PROCESS])
async def test_transport_loss_reaps_child_and_preserves_workspace_until_leave(
    host: RetentionHost,
    loss: PeerCommand,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    original = Path(host.workspace("room-a"))
    host.root = host.root / "changed"
    with pytest.raises((TurnResultAlreadyReported, CLIConnectionError)):
        await host.send("room-a", loss)
    await host.settled("room-a")
    assert host.exited == [True]
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
    assert host.exited == [True, False]

    new_directory = Path(host.workspace("room-a"))
    new_directory.mkdir(parents=True)
    (new_directory / "notes.txt").write_text("bravo")
    await host.leave("room-a")
    assert await host.send("room-a", PeerCommand.READ) == ["Read: bravo"]
    assert (original / "notes.txt").read_text() == "alpha"


async def test_failed_startup_cleanup_blocks_replacement_until_retry(
    host: RetentionHost,
) -> None:
    host.fail_startup.touch()
    host.hold_startup.touch()
    opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
    while not (Path(host.workspace("room-a")) / "starting.txt").exists():
        await asyncio.sleep(0.01)
    host.children[0].refuse_close = True
    host.hold_startup.unlink()
    # The opening turn reports its own failure; Codex reports the failed
    # cleanup that follows to the next operation.
    with pytest.raises(RuntimeError, match="startup failed|cleanup failed"):
        await opening
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    host.fail_startup.unlink()
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.send("room-a", write_instruction("alpha"))
    assert host.exited == [False]
    host.children[0].refuse_close = False
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    assert host.exited == [True, False]


async def test_transport_loss_with_failed_cleanup_can_recover_safely(
    host: RetentionHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.children[0].refuse_close = True
    with pytest.raises((RuntimeError, TurnResultAlreadyReported)):
        await host.send("room-a", PeerCommand.LOSE_TRANSPORT)
    await host.settled("room-a")
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.send("room-a", PeerCommand.READ)
    assert len(host.children) == 1
    host.children[0].refuse_close = False
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
    assert host.exited == [True, False]


@pytest.mark.parametrize("host", ["codex"], indirect=True)
async def test_close_timeout_retains_ownership_until_retry(
    host: RetentionHost,
) -> None:
    assert isinstance(host.adapter, CodexAdapter)
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.adapter.config = host.adapter.config.model_copy(
        update={"client_close_timeout_s": 0.05}
    )
    barrier = host.children[0].closing = Hold()
    with pytest.raises(TimeoutError):
        await host.leave("room-a")
    await barrier.reached.wait()
    assert host.exited == [False]
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    with pytest.raises(InputNotAccepted, match="is closing"):
        await host.send("room-a", write_instruction("changed"))
    assert host.exited == [False]
    host.adapter.config = host.adapter.config.model_copy(
        update={"client_close_timeout_s": None}
    )
    barrier.released.set()
    await host.leave("room-a")
    assert host.exited == [True]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]


@pytest.mark.parametrize("host", ["codex"], indirect=True)
@pytest.mark.parametrize("loss", [PeerCommand.LOSE_TRANSPORT, PeerCommand.EXIT_PROCESS])
async def test_a_replacement_process_continues_the_same_native_conversation(
    host: RetentionHost, loss: PeerCommand
) -> None:
    """The memo lives only in the thread's private context, so recalling it
    on a new process proves the native conversation itself continued."""
    assert await host.send("room-a", memo_instruction("private-7")) == ["Noted."]
    thread = host.native_id("room-a")
    with pytest.raises(TurnResultAlreadyReported):
        await host.send("room-a", loss)
    await host.settled("room-a")

    assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: private-7"]
    assert host.native_id("room-a") == thread
    assert host.exited == [True, False]
    assert host.overlaps == 0


@pytest.mark.parametrize("host", ["codex"], indirect=True)
async def test_a_refused_restore_keeps_the_conversation_and_starts_no_other(
    host: RetentionHost,
) -> None:
    assert await host.send("room-a", memo_instruction("private-7")) == ["Noted."]
    thread = host.native_id("room-a")
    with pytest.raises(TurnResultAlreadyReported):
        await host.send("room-a", PeerCommand.LOSE_TRANSPORT)
    await host.settled("room-a")
    saved = host.root / "codex-home" / f"{thread}.json"
    kept = saved.read_text()
    saved.unlink()

    for _ in range(2):
        with pytest.raises(CodexJsonRpcError, match="no rollout found"):
            await host.send("room-a", PeerCommand.RECALL)
        await host.settled("room-a")
        assert host.native_id("room-a") == thread
    assert sorted(path.name for path in saved.parent.iterdir()) == []

    saved.write_text(kept)
    assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: private-7"]
    assert host.overlaps == 0


@pytest.mark.parametrize("host", ["codex"], indirect=True)
async def test_restored_threads_refuse_tools_they_were_not_started_with(
    host: RetentionHost,
) -> None:
    assert isinstance(host.adapter, CodexAdapter)
    started_with = await host.send("room-a", PeerCommand.TOOLS)
    thread = host.native_id("room-a")
    with pytest.raises(TurnResultAlreadyReported):
        await host.send("room-a", PeerCommand.LOSE_TRANSPORT)
    await host.settled("room-a")
    original = host.adapter.config
    host.adapter.config = original.model_copy(
        update={
            "additional_dynamic_tools": [
                {"name": "late_tool", "description": "", "inputSchema": {}}
            ]
        }
    )

    with pytest.raises(CodexContinuityError, match="late_tool"):
        await host.send("room-a", PeerCommand.TOOLS)
    assert host.native_id("room-a") == thread

    host.adapter.config = original
    assert await host.send("room-a", PeerCommand.TOOLS) == started_with
    archive = f"/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}"
    await host.send("room-a", archive)
    assert host.native_id("room-a") is None


@pytest.mark.parametrize("host", ["codex"], indirect=True)
async def test_rooms_progress_while_another_room_starts_or_closes(
    host: RetentionHost,
) -> None:
    host.shared = False
    starting = host.next_start = Hold()
    opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
    async with starting:
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
    assert await opening == ["alpha"]

    closing = host.children[0].closing = Hold()
    leaving = asyncio.create_task(host.leave("room-a"))
    async with closing:
        assert await host.send("room-b", PeerCommand.READ) == ["Read: bravo"]
    await leaving
    assert host.exited == [True, False]


@pytest.mark.parametrize("host", ["codex"], indirect=True)
async def test_leaving_during_startup_closes_the_half_started_process(
    host: RetentionHost,
) -> None:
    assert isinstance(host.adapter, CodexAdapter)
    host.adapter.config = host.adapter.config.model_copy(
        update={"turn_settle_timeout_s": 0.01}
    )
    starting = host.next_start = Hold()
    opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
    await starting.reached.wait()

    await host.leave("room-a")

    with pytest.raises(asyncio.CancelledError):
        await opening
    assert host.exited == [True]
    assert host.native_id("room-a") is None
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
