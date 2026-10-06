"""One workspace ownership contract through both adapters and real children."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError

from band.adapters.claude_sdk import (
    ClaudeApprovalOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.integrations.claude_sdk import transport
from band.integrations.codex import CodexStdioClient
from band.testing import FakeAgentTools
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.process import WorkspacePeer
from tests.adapters.roompeer import PeerCommand, write_instruction
from tests.paths import REPO_ROOT


class CodexPeer(CodexStdioClient):
    """Inject a failed or held close at the real transport boundary."""

    refuse_close = False
    closing: Hold | None = None

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
class WorkspaceHost:
    adapter: CodexAdapter | ClaudeSDKAdapter
    root: Path
    fail_startup: Path
    hold_startup: Path
    shared: bool = True
    children: list[CodexPeer | ClaudePeer] = field(default_factory=list)

    def workspace(self, room_id: str) -> str:
        return str(self.root / ("shared" if self.shared else room_id))

    async def send(self, room_id: str, content: str) -> list[str]:
        tools = FakeAgentTools(room_id=room_id)
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

    @property
    def exited(self) -> list[bool]:
        return [child.exited for child in self.children]


@pytest_asyncio.fixture(params=["codex", "claude"], loop_scope="function")
async def host(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[WorkspaceHost]:
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
            host.children.append(child)
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
            host.children.append(child)
            return child

        monkeypatch.setattr(transport, "SubprocessCLITransport", claude_child)
    host = WorkspaceHost(adapter, tmp_path, failure, hold)
    await adapter.on_started("Peer", "A subprocess under test")
    try:
        yield host
    finally:
        failure.unlink(missing_ok=True)
        hold.unlink(missing_ok=True)
        for child in host.children:
            child.refuse_close = False
            if child.closing is not None:
                child.closing.released.set()
        try:
            await adapter.cleanup_all()
        finally:
            for child in host.children:
                await child.close()


async def test_failed_cleanup_retains_ownership_and_blocks_replacement(
    host: WorkspaceHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.children[0].refuse_close = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.adapter.on_cleanup("room-a")
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.send("room-a", write_instruction("changed"))
    assert host.exited == [False]
    assert (Path(host.workspace("room-a")) / "notes.txt").read_text() == "alpha"
    host.children[0].refuse_close = False
    await host.adapter.on_cleanup("room-a")
    assert host.exited == [True]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
    assert host.exited == [True, False]


async def test_failed_startup_keeps_membership_claim_until_leave(
    host: WorkspaceHost,
) -> None:
    host.fail_startup.touch()
    with pytest.raises(Exception, match="startup failed"):
        await host.send("room-a", write_instruction("alpha"))
    assert host.exited == [True]
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    await host.adapter.on_cleanup("room-a")
    host.fail_startup.unlink()
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]


async def test_failed_room_cleanup_can_recover_in_the_same_workspace(
    host: WorkspaceHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.children[0].refuse_close = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.adapter.on_cleanup("room-a")
    host.children[0].refuse_close = False
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
    assert host.exited == [True, False]
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))


async def test_cancelled_startup_keeps_ownership_and_can_recover(
    host: WorkspaceHost,
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
    await host.adapter.on_cleanup("room-a")
    assert all(host.exited)


async def test_cancelled_cleanup_can_be_completed_before_transfer(
    host: WorkspaceHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    barrier = host.children[0].closing = Hold()
    leaving = asyncio.create_task(host.adapter.on_cleanup("room-a"))
    async with barrier:
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving
        assert host.exited == [False]
    await host.adapter.on_cleanup("room-a")
    assert host.exited == [True]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]


async def test_shutdown_attempts_every_room_and_retains_failed_children(
    host: WorkspaceHost,
) -> None:
    host.shared = False
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
    host.children[0].refuse_close = True
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        await host.adapter.cleanup_all()
    assert host.exited == [False, True]
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        await host.adapter.on_started("Peer", "Restarting after failed cleanup")
    assert host.exited == [False, True]
    host.children[0].refuse_close = False
    await host.adapter.on_started("Peer", "Restarting after successful cleanup")
    assert host.exited == [True, True]
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]


async def test_transport_loss_reaps_child_and_preserves_workspace_until_leave(
    host: WorkspaceHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    original = Path(host.workspace("room-a"))
    host.root = host.root / "changed"
    with pytest.raises((TurnResultAlreadyReported, CLIConnectionError)):
        await host.send("room-a", PeerCommand.LOSE_STDOUT)
    assert host.exited == [True]
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
    assert host.exited == [True, False]

    new_directory = Path(host.workspace("room-a"))
    new_directory.mkdir(parents=True)
    (new_directory / "notes.txt").write_text("bravo")
    await host.adapter.on_cleanup("room-a")
    assert await host.send("room-a", PeerCommand.READ) == ["Read: bravo"]
    assert (original / "notes.txt").read_text() == "alpha"


async def test_failed_startup_cleanup_blocks_replacement_until_retry(
    host: WorkspaceHost,
) -> None:
    host.fail_startup.touch()
    host.hold_startup.touch()
    opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
    while not (Path(host.workspace("room-a")) / "starting.txt").exists():
        await asyncio.sleep(0.01)
    host.children[0].refuse_close = True
    host.hold_startup.unlink()
    with pytest.raises(RuntimeError, match="cleanup failed"):
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
    host: WorkspaceHost,
) -> None:
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.children[0].refuse_close = True
    with pytest.raises((RuntimeError, TurnResultAlreadyReported)):
        await host.send("room-a", PeerCommand.LOSE_STDOUT)
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await host.send("room-a", PeerCommand.READ)
    assert len(host.children) == 1
    host.children[0].refuse_close = False
    assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
    assert host.exited == [True, False]


@pytest.mark.parametrize("host", ["codex"], indirect=True)
async def test_close_timeout_retains_ownership_until_retry(host: WorkspaceHost) -> None:
    assert isinstance(host.adapter, CodexAdapter)
    assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
    host.adapter.config = host.adapter.config.model_copy(
        update={"client_close_timeout_s": 0.05}
    )
    barrier = host.children[0].closing = Hold()
    with pytest.raises(TimeoutError):
        await host.adapter.on_cleanup("room-a")
    await barrier.reached.wait()
    assert host.exited == [False]
    with pytest.raises(ValueError, match="both"):
        await host.send("room-b", write_instruction("bravo"))
    with pytest.raises(TimeoutError):
        await host.send("room-a", write_instruction("changed"))
    assert host.exited == [False]
    host.adapter.config = host.adapter.config.model_copy(
        update={"client_close_timeout_s": None}
    )
    barrier.released.set()
    await host.adapter.on_cleanup("room-a")
    assert host.exited == [True]
    assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
