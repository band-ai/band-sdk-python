"""The native session resume rule shared by adapters that resume a session.

A room's resumable session is the newest one whose seeding prompt (the first,
context-carrying one) completed; its resume record reaches room history only
after that, whatever the emit setting. A session is seeded only when its first
prompt completed; a cancelled or interrupted first prompt is unseeded like a
failed one. An unseeded session is dropped: nothing is recorded, and the next
prompt starts a fresh session that replays the room's transcript. On start an
adapter restores the newest recorded session; when there is none, or the agent
cannot restore it, the adapter starts a fresh session and replays the room's
transcript into it.

The native restore call, its failure signal and how a seed is framed stay with
each adapter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import MessageType, PlatformMessage
from band.runtime.formatters import format_history_for_llm, messages_before


@dataclass
class SessionSeed:
    """Whether a room's native session already holds the room's context.

    A restored session kept its own. A fresh one is owed the transcript in its
    seeding prompt and is seeded only when that first prompt completed; a
    cancelled or interrupted first prompt is unseeded like a failed one. An
    unseeded session is dropped: nothing is recorded, and the next prompt
    starts a fresh session that replays the room's transcript. ``carried`` is
    false when the pending prompt lacks the transcript, so its completion
    seeds nothing.
    """

    restored: bool = False
    seeded: bool = False
    carried: bool = True

    @property
    def replay_due(self) -> bool:
        """Whether the next prompt must replay the room's transcript."""
        return not (self.restored or self.seeded)

    def confirm(self) -> None:
        """Record that the session's seeding prompt completed."""
        self.seeded = self.seeded or self.carried


@dataclass(frozen=True)
class ResumeRecord:
    """One adapter's resume record: the task event naming a room's session."""

    label: str
    room_key: str
    session_key: str

    def candidates(self, raw: Iterable[Mapping[str, Any]]) -> dict[str, str]:
        """Each room's newest recorded session in ``raw`` history."""
        sessions: dict[str, str] = {}
        for message in raw:
            metadata = message.get("metadata")
            if not self.matches(message) or not isinstance(metadata, Mapping):
                continue
            room = metadata.get(self.room_key)
            session = metadata.get(self.session_key)
            if isinstance(room, str) and isinstance(session, str) and session:
                sessions[room] = session
        return sessions

    def matches(self, message: Mapping[str, Any]) -> bool:
        """Match this record's message type and label."""
        return (
            message.get("message_type") == MessageType.TASK
            and message.get("content") == self.label
        )

    async def post(
        self,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        seed: SessionSeed,
        session: str | None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> bool:
        """Post the room's record of ``session`` once its seed is confirmed.

        An empty ``session`` is left out of the record; ``details`` carries
        any adapter-specific extras. Returns whether the record reached the
        room.

        The record is resume state rather than narration, so no emit setting
        gates it. Adapters post it after every completed prompt, or until
        it lands, so a failed post is retried.
        """
        if not seed.seeded:
            return False
        return await send_event_safe(
            tools,
            self.label,
            MessageType.TASK,
            metadata={
                self.room_key: room_id,
                **({self.session_key: session} if session else {}),
                **(details or {}),
            },
            log_label=f"{self.label} for room {room_id}",
        )


async def fetch_transcript(
    tools: AgentToolsProtocol, *, room_id: str, messages: Sequence[PlatformMessage]
) -> list[dict[str, Any]]:
    """The room's history before the turn's earliest input, every page, in
    the shape bootstrap hands converters (mentions resolved to handles).

    The runtime hands an adapter the room's history only on session
    bootstrap, so a session that must be seeded later fetches it here.
    """
    page = 1
    collected: list[dict[str, Any]] = []
    while True:
        context = await tools.fetch_room_context(room_id=room_id, page=page)
        data = context.get("data") or []
        if not data:
            break
        collected.extend(data)
        meta = context.get("meta") or {}
        total_pages = meta.get("total_pages")
        if isinstance(total_pages, int):
            if page >= total_pages:
                break
        elif len(data) < int(meta.get("page_size") or len(data)):
            break
        page += 1
    return format_history_for_llm(
        messages_before(collected, messages), participants=tools.participants
    )
