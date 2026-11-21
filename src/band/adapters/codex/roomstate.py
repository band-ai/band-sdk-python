"""One Band room's Codex process and thread."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields
from dataclasses import field as dataclass_field
from typing import Any

from band.adapters.codex.rpc import CodexClientProtocol
from band.adapters.codex.turnstate import NativeTurn
from band.core.protocols import AgentToolsProtocol
from band.core.retention import RetainedRoom
from band.integrations.codex.activity import CommandResource
from band.runtime.sessionresume import SessionSeed


@dataclass(frozen=True)
class ThreadSettings:
    """Settings Codex reports for a thread that ``turn/start`` does not re-send;
    a restored thread must report the ones it was started with."""

    model_provider: str | None = None
    cwd: str | None = None

    @classmethod
    def reported_by(cls, result: dict[str, Any]) -> ThreadSettings:
        provider, cwd = result.get("modelProvider"), result.get("cwd")
        return cls(
            model_provider=provider if isinstance(provider, str) else None,
            cwd=cwd if isinstance(cwd, str) else None,
        )

    def changes_in(self, restored: ThreadSettings) -> list[str]:
        return [
            f"{setting.name} {retained!r} became {current!r}"
            for setting in fields(self)
            if (retained := getattr(self, setting.name)) is not None
            and (current := getattr(restored, setting.name)) != retained
        ]


@dataclass(frozen=True)
class HistoryResume:
    """Whether a recorded thread still has its one retry available."""

    thread_id: str
    retry_due: bool


@dataclass
class RoomCodexClient:
    """One Band room's Codex process and what its thread was started with.

    ``retained`` owns admission and every physical transition. ``client`` and
    ``reader`` are cleared only once the process is confirmed closed; the
    room's thread id stays in the adapter's ``_room_threads`` across process
    replacement until the room is finally disposed.
    """

    room_id: str
    workspace: str
    retained: RetainedRoom = dataclass_field(init=False)
    client: CodexClientProtocol | None = None
    reader: asyncio.Task[None] | None = None
    turn: NativeTurn | None = None
    commands: dict[tuple[str, str, str], CommandResource] = dataclass_field(
        default_factory=dict
    )
    item_activity: dict[tuple[str, str, str], tuple[AgentToolsProtocol, object]] = (
        dataclass_field(default_factory=dict)
    )
    routing_lock: asyncio.Lock = dataclass_field(default_factory=asyncio.Lock)
    selected_model: str | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    # The dynamic tools the thread was started with: resume restores these and
    # ignores any others. None when the thread came from Band history.
    registration: list[dict[str, Any]] | None = None
    settings: ThreadSettings = ThreadSettings()
    # The thread was resumed into this process; a turn confirms its tools.
    restored: bool = False
    # Whether the thread holds the room's context yet.
    seed: SessionSeed = dataclass_field(default_factory=SessionSeed)
    # Band history names the thread: its resume record was posted.
    recorded: bool = False
    # Resolve repeated history against the room's retry or archive decision.
    history_resume: HistoryResume | None = None
    # Runtime can commit this context before a native turn receives it.
    participants_due: bool = False
    pending_system_context: list[str] = dataclass_field(default_factory=list)
    # Serializes this room's turns, local commands and their preparation; a
    # turn parked on a manual approval blocks only this room. Approval
    # replies are handled without it so they can resolve that wait.
    rpc_lock: asyncio.Lock = dataclass_field(default_factory=asyncio.Lock)

    @property
    def generation(self) -> int:
        """The residency the room's current process belongs to."""
        return self.retained.generation

    def bind_thread(
        self,
        *,
        registration: list[dict[str, Any]] | None,
        settings: ThreadSettings,
        seed: SessionSeed,
    ) -> None:
        """Record what the thread this process now runs was started with."""
        self.registration = registration
        self.settings = settings
        self.seed = seed
        self.restored = False
        self.recorded = False

    def forget_thread(self) -> None:
        self.bind_thread(
            registration=None, settings=ThreadSettings(), seed=SessionSeed()
        )

    def take_history_resume(self, recorded: str | None) -> tuple[str | None, bool]:
        """Pick the recorded thread and spend its pending retry before sending."""
        retry = self.history_resume
        if retry is None:
            return recorded, False
        if recorded is not None and recorded != retry.thread_id:
            self.history_resume = None
            return recorded, False
        if not retry.retry_due:
            return None, False
        # The retry is spent even if STOP lands before its reply.
        self.history_resume = HistoryResume(retry.thread_id, retry_due=False)
        return retry.thread_id, True

    def archive_thread(self, mapped_thread: str | None) -> None:
        """Forget the thread and keep repeated history from restoring it."""
        thread_id = mapped_thread
        if thread_id is None and self.history_resume is not None:
            thread_id = self.history_resume.thread_id
        if thread_id is not None:
            self.history_resume = HistoryResume(thread_id, retry_due=False)
        self.forget_thread()

    async def retain_system_context(
        self, contacts: str | None, participants: str | None
    ) -> None:
        """Retain context the runtime may commit while provider startup fails."""
        if contacts:
            self.pending_system_context.append(contacts)
        if participants is not None:
            self.participants_due = True

    def require_client(self) -> CodexClientProtocol:
        if self.client is None:
            raise RuntimeError(f"Room {self.room_id} has no Codex process")
        return self.client

    async def stop_reader(self) -> None:
        if (reader := self.reader) is None:
            return
        reader.cancel()
        await asyncio.wait({reader})
        self.reader = None
