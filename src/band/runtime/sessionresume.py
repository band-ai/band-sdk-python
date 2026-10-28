"""The native session resume rule shared by adapters that resume a session.

A room's resumable session is the newest one whose seeding prompt (the first,
context-carrying one) completed; its resume record reaches room history only
after that, whatever the emit setting. On start an adapter restores the newest
recorded session; when there is none, or the agent cannot restore it, the
adapter starts a fresh session and replays the room's transcript into it.

The native restore call, its failure signal and how a seed is framed stay with
each adapter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import MessageType


@dataclass
class SessionSeed:
    """Whether a room's native session already holds the room's context.

    A restored session kept its own. A fresh one is owed the transcript in its
    seeding prompt and becomes resumable only once that prompt completes; a
    failed or refused seeding prompt leaves it unseeded.
    """

    restored: bool = False
    seeded: bool = False

    @property
    def replay_due(self) -> bool:
        """Whether the next prompt must replay the room's transcript."""
        return not (self.restored or self.seeded)

    def confirm(self) -> None:
        """Record that the session's seeding prompt completed."""
        self.seeded = True


@dataclass(frozen=True)
class ResumeRecord:
    """One adapter's resume record: the task event naming a room's session."""

    label: str
    room_key: str
    session_key: str

    def candidates(self, raw: Iterable[Mapping[str, Any]]) -> dict[str, str]:
        """Each room's newest recorded session in ``raw`` history.

        A record without the session key leaves its room's candidate as it
        was; one whose session is empty or unusable clears it.
        """
        sessions: dict[str, str] = {}
        for message in raw:
            match message.get("message_type"), message.get("metadata"):
                case (
                    MessageType.TASK,
                    {self.room_key: str() as room, self.session_key: str() as session},
                ) if session:
                    sessions[room] = session
                case MessageType.TASK, {
                    self.room_key: str() as room,
                    self.session_key: _,
                }:
                    sessions.pop(room, None)
        return sessions

    async def post(
        self,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        seed: SessionSeed,
        details: Mapping[str, JsonValue],
    ) -> None:
        """Post the room's record with ``details`` once its seed is confirmed.

        The record is resume state rather than narration, so no emit setting
        gates it. Adapters post it after every completed prompt, which also
        retries a post that failed before.
        """
        if not seed.seeded:
            return
        await send_event_safe(
            tools,
            self.label,
            MessageType.TASK,
            metadata={self.room_key: room_id, **details},
            log_label=self.label,
        )
