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
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import MessageType
from band.runtime.formatters import messages_before


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
        """Each room's newest recorded session in ``raw`` history."""
        sessions: dict[str, str] = {}
        for message in raw:
            metadata = message.get("metadata")
            if message.get("message_type") != MessageType.TASK or not isinstance(
                metadata, Mapping
            ):
                continue
            room = metadata.get(self.room_key)
            session = metadata.get(self.session_key)
            if isinstance(room, str) and isinstance(session, str) and session:
                sessions[room] = session
        return sessions

    async def post(
        self,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        seed: SessionSeed,
        session: str | None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """Post the room's record of ``session`` once its seed is confirmed.

        An empty ``session`` is left out of the record; ``details`` carries
        any adapter-specific extras.

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
            metadata={
                self.room_key: room_id,
                **({self.session_key: session} if session else {}),
                **(details or {}),
            },
            log_label=self.label,
        )


async def fetch_transcript(
    tools: AgentToolsProtocol, *, room_id: str, turn_ids: AbstractSet[str]
) -> list[dict[str, Any]]:
    """The room's raw history before the turn's earliest input, every page.

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
    return messages_before(collected, turn_ids)
