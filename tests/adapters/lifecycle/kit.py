"""Shared doubles and helpers for the Room workspace lifecycle tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.codex import CodexAdapter
from band.core.retention import Residency, RetainedRoom
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.integrations.codex import CodexStdioClient
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.process import WorkspacePeer
from tests.framework_conformance.turnprobes import DispatchingFakeTools

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
    """Inject a held start or a held close at the real transport."""

    starting: Hold | None = None
    closing: Hold | None = None

    async def connect(self) -> None:
        await super().connect()
        if self.starting is not None:
            self.starting.reached.set()
            await self.starting.released.wait()

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
    # The room's retained residency, for adapters that retain rooms.
    residency: Callable[[str], RetainedRoom | None]
    shared: bool = True
    children: list[CodexPeer | ClaudePeer] = field(default_factory=list)
    child_cwds: list[str | None] = field(default_factory=list)
    # Children started while an earlier child in the same workspace still ran.
    overlaps: int = 0
    next_start: Hold | None = None

    def workspace(self, room_id: str) -> str:
        return str(self.root / ("shared" if self.shared else room_id))

    def spawned(self, child: CodexPeer | ClaudePeer, cwd: str | None) -> None:
        self.overlaps += any(
            not earlier.exited and earlier_cwd == cwd
            for earlier, earlier_cwd in zip(self.children, self.child_cwds, strict=True)
        )
        self.children.append(child)
        self.child_cwds.append(cwd)

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
        if (residency := self.residency(room_id)) is None:
            return
        for _ in range(MAX_SETTLE_POLLS):
            if residency.snapshot.state not in IN_FLIGHT:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Room {room_id} never settled")

    def native_id(self, room_id: str) -> str | None:
        assert isinstance(self.adapter, CodexAdapter), "Codex projection only"
        return self.adapter._room_threads.get(room_id)

    @property
    def exited(self) -> list[bool]:
        return [child.exited for child in self.children]
