"""Transcript reads for the Desktop room view, proven current by the relay."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from band.client.rest import DEFAULT_REQUEST_OPTIONS, AsyncRestClient, ChatRoomRequest
from band.integrations.desktop_app.event_relay import RelayStatus, RoomEventBroker
from band.integrations.desktop_app.room import (
    EPOCH,
    AgentIdentity,
    HostProfile,
    RoomEvent,
    RoomMessage,
    RoomParticipant,
    RoomTranscript,
    parse_timestamp,
)
from band.integrations.desktop_app.prompts import (
    ambiguous_room_guidance,
    room_briefing,
    unknown_room_guidance,
)
from band.integrations.desktop_app.settings import RoomViewTuning
from band.integrations.desktop_app.wakes import WakeLedger
from band.runtime.tools import AgentTools, iter_chat_pages, serialize_tool_result

logger = logging.getLogger(__name__)

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# How far back one read may page. A cursor buried deeper than this is a return
# from an absence long enough that the room has moved on; the read says so in
# the log rather than paging a whole history into a single monitor tick.
MAX_TRANSCRIPT_PAGES = 20


def covers(tail: list[RoomMessage], *, after: datetime | None, limit: int) -> bool:
    """Whether the tail collected so far already holds all a read owes."""
    if after is None:
        return len(tail) >= limit
    return bool(tail) and tail[0].at <= after


class TranscriptTools(Protocol):
    async def get_agent_profile(self) -> dict[str, Any]: ...

    async def create_room(self, task_id: str | None = None) -> str: ...

    async def list_rooms(self) -> list[dict[str, Any]]: ...

    async def list_participants(self, chat_id: str) -> list[dict[str, Any]]: ...

    async def list_agent_context(
        self,
        chat_id: str,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class AgentTranscriptTools:
    """The Desktop room view's reads, over the SDK's room-bound AgentTools."""

    def __init__(self, rest: AsyncRestClient) -> None:
        self._rest = rest
        self._rooms: dict[str, AgentTools] = {}

    def _room(self, chat_id: str) -> AgentTools:
        if chat_id not in self._rooms:
            self._rooms[chat_id] = AgentTools(chat_id, self._rest)
        return self._rooms[chat_id]

    async def get_agent_profile(self) -> dict[str, Any]:
        response = await self._rest.agent_api_identity.get_agent_me(
            request_options=DEFAULT_REQUEST_OPTIONS
        )
        serialized = serialize_tool_result(response)
        return serialized.get("data") or serialized

    async def create_room(self, task_id: str | None = None) -> str:
        chat = ChatRoomRequest(task_id=task_id) if task_id else ChatRoomRequest()
        response = await self._rest.agent_api_chats.create_agent_chat(
            chat=chat,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return str(response.data.id)

    async def list_rooms(self) -> list[dict[str, Any]]:
        async def fetch(page: int, page_size: int) -> Any:
            return await self._rest.agent_api_chats.list_agent_chats(
                page=page,
                page_size=page_size,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )

        return [
            serialize_tool_result(item)
            async for response in iter_chat_pages(fetch)
            for item in (response.data or [])
        ]

    async def list_participants(self, chat_id: str) -> list[dict[str, Any]]:
        participants = await self._room(chat_id).get_participants()
        return [serialize_tool_result(item) for item in (participants or [])]

    async def list_agent_context(
        self,
        chat_id: str,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = await self._room(chat_id).fetch_room_context(
            room_id=chat_id,
            page=page,
            page_size=page_size,
        )
        return context["data"], context["meta"]


@dataclass
class ReadPulse:
    """Proof of how current a room's last REST read still is.

    ``version`` is the broker's event counter sampled before the read, and
    ``newest_message_at`` the newest message any read of this room has ever
    returned. Together they let a later caller prove no unseen message can
    exist for it, and skip the read entirely.
    """

    version: int
    newest_message_at: datetime
    snapshot: RoomTranscript


class RoomTranscriptService:
    """Reads the agent-relevant room transcript."""

    def __init__(
        self,
        tools: TranscriptTools,
        *,
        viewer: AgentIdentity | None = None,
        events: RoomEventBroker | None = None,
        transport: RelayStatus | None = None,
        tuning: RoomViewTuning | None = None,
    ) -> None:
        self._tools = tools
        self._viewer = viewer
        self._events = events
        self._transport = transport or RelayStatus()
        self.tuning = tuning or RoomViewTuning()
        self._participants: dict[str, list[RoomParticipant]] = {}
        self._announced_rooms: set[str] = set()
        self._pulses: dict[str, ReadPulse] = {}
        self.wakes = WakeLedger()
        self.host = HostProfile()

    async def viewer(self) -> AgentIdentity:
        """The Band agent identity Claude Desktop operates as."""
        if self._viewer is None:
            await self.refresh_viewer()
        assert self._viewer is not None
        return self._viewer

    async def refresh_viewer(self) -> AgentIdentity:
        """Refresh the connected agent identity from ``/api/v1/agent/me``."""
        self._viewer = AgentIdentity.model_validate(
            await self._tools.get_agent_profile()
        )
        return self._viewer

    async def create_room(self, task_id: str | None = None) -> str:
        """Create a room as the connected agent."""
        return await self._tools.create_room(task_id)

    async def resolve_room(self, room: str) -> str:
        """The room ID a join argument names.

        A UUID passes through untouched; anything else is matched against the
        agent's rooms by exact ID, then by title. A miss raises with the full
        room list so the model can offer the real options — or creating a room
        — instead of failing dumbly.
        """
        query = room.strip()
        if UUID_PATTERN.match(query):
            return query
        rooms = await self._tools.list_rooms()
        if any(str(item.get("id")) == query for item in rooms):
            return query
        matches = [
            item
            for item in rooms
            if query.lower() in str(item.get("title") or "").lower()
        ]
        if len(matches) == 1:
            return str(matches[0]["id"])
        if matches:
            raise ValueError(ambiguous_room_guidance(room, matches))
        raise ValueError(unknown_room_guidance(room, rooms))

    async def participants(
        self,
        chat_id: str,
        *,
        refresh: bool = False,
    ) -> list[RoomParticipant]:
        """The room roster Claude is told about, cached between polls."""
        if refresh or chat_id not in self._participants:
            try:
                self._participants[chat_id] = [
                    RoomParticipant.model_validate(item)
                    for item in await self._tools.list_participants(chat_id)
                ]
            except Exception:
                logger.warning("Could not read Band room participants", exc_info=True)
                self._participants.setdefault(chat_id, [])
        return self._participants[chat_id]

    def release_wakes(self, chat_id: str, message_ids: list[str]) -> None:
        """Re-offer wakes the host refused, from messages the last read saw."""
        pulse = self._pulses.get(chat_id)
        known = {
            message.id: message
            for message in (pulse.snapshot.messages if pulse else [])
        }
        self.wakes.release(chat_id, message_ids, known)

    def capture_host(self, params: Any) -> None:
        """Record the host's declared capabilities, once, from any tool call."""
        if self.host.captured or params is None:
            return
        self.host = HostProfile.from_client_params(params)
        logger.info("Desktop host capabilities: %s", self.host.model_dump())

    def unannounced_rooms(self, current_chat_id: str) -> list[str]:
        """Rooms the agent was added to that the agent has not been told about.

        The room view watches one room per conversation, so this is not an
        invitation to join them — it is so the agent can tell its user that
        somewhere else now expects it.
        """
        fresh = [
            room
            for room in self._transport.rooms_added
            if room != current_chat_id and room not in self._announced_rooms
        ]
        self._announced_rooms.update(fresh)
        return fresh

    async def _page(self, chat_id: str, number: int) -> list[dict[str, Any]]:
        items, _ = await self._tools.list_agent_context(
            chat_id,
            page=number,
            page_size=self.tuning.band_transcript_page_size,
        )
        return items

    async def _messages_since(
        self,
        chat_id: str,
        *,
        after: datetime | None,
        limit: int,
    ) -> list[RoomMessage]:
        """The agent-visible messages a caller resuming at ``after`` is owed.

        The context API pages oldest first, so the newest messages sit on the
        last page and this walks back from there, stopping as soon as the tail
        reaches past the cursor. Reading only the newest page would silently
        drop whatever a long absence buried behind it, while still advancing
        the cursor past it. With no cursor there is nothing to reach back to,
        so the newest ``limit`` messages are the whole read.
        """
        first, metadata = await self._tools.list_agent_context(
            chat_id,
            page=1,
            page_size=self.tuning.band_transcript_page_size,
        )
        total_pages = max(int(metadata.get("total_pages") or 1), 1)
        budget_stops_at = max(1, total_pages - MAX_TRANSCRIPT_PAGES + 1)

        tail: list[RoomMessage] = []
        for number in range(total_pages, budget_stops_at - 1, -1):
            items = first if number == 1 else await self._page(chat_id, number)
            tail = [RoomMessage.model_validate(item) for item in items] + tail
            if covers(tail, after=after, limit=limit):
                break
        else:
            if budget_stops_at > 1:
                logger.warning(
                    "Read of room %s stopped at its %d page budget; anything "
                    "older than page %d was not read",
                    chat_id,
                    MAX_TRANSCRIPT_PAGES,
                    budget_stops_at,
                )
        return tail if after is not None else tail[-limit:]

    async def read(
        self,
        chat_id: str,
        *,
        since: str | None = None,
        refresh_participants: bool = False,
    ) -> RoomTranscript:
        """The agent-visible room state, newest last, annotated for the agent."""
        # Captured before the read: a message inserted while it runs carries a
        # later timestamp, so resuming from here cannot skip it.
        started_at = datetime.now(timezone.utc)
        viewer = await self.viewer()
        roster = await self.participants(chat_id, refresh=refresh_participants)
        after = parse_timestamp(since)

        seen: set[str] = set()
        messages: list[RoomMessage] = []
        for message in await self._messages_since(
            chat_id,
            after=after,
            limit=self.tuning.band_initial_transcript_messages,
        ):
            if message.id and message.id in seen:
                continue
            seen.add(message.id)
            if after and message.at <= after:
                continue
            message.truncate(self.tuning.band_max_message_chars)
            message.addressed_to_viewer = message.addresses(viewer)
            message.render_mentions(roster)
            messages.append(message)
        messages.sort(key=lambda message: (message.at, message.id))

        # Only the opening read needs the heuristic that the agent's last
        # outbound message closes the asks before it. On a resumed read every
        # message is newer than anything the agent has been shown, so a reply
        # it sent to one peer cannot have answered another peer's ask.
        answered_through = (
            EPOCH
            if after is not None
            else max(
                (message.at for message in messages if message.sender_id == viewer.id),
                default=EPOCH,
            )
        )
        transcript = RoomTranscript(
            chat_id=chat_id,
            viewer=viewer,
            participants=roster,
            next_since=messages[-1].at if messages else max(started_at, after or EPOCH),
            messages=messages,
            pending_requests=[
                message
                for message in messages
                if message.addressed_to_viewer
                and message.sender_id != viewer.id
                and message.at > answered_through
                and message.is_text
            ],
        )
        transcript.transport = self._transport
        transcript.host = self.host
        transcript.role_briefing = room_briefing(transcript)
        return transcript

    async def wait_for_room_event(
        self,
        chat_id: str,
        *,
        since: str | None,
        timeout_seconds: int,
    ) -> RoomEvent:
        """Wait on the SDK WebSocket, then hydrate the new agent context.

        A quiet tick on a live WebSocket costs no REST. The broker versions
        every room event, so an unchanged version proves the last read is still
        complete, and a caller whose cursor has passed every message that read
        ever saw provably has nothing new to fetch. When the WebSocket is down
        every tick reads — REST polling as the explicit degraded mode, not the
        permanent default.
        """
        if self._events is None:
            raise RuntimeError("Room WebSocket events are not configured.")

        version = self._events.version(chat_id)
        started = datetime.now(timezone.utc)
        after = parse_timestamp(since)

        current = self._read_is_current(chat_id, version, after)
        logger.debug(
            "wait chat=%s version=%d read_skipped=%s", chat_id, version, current
        )
        if not current:
            transcript = await self._verified_read(
                chat_id, since=since, version=version
            )
            if transcript.messages:
                return RoomEvent(**dict(transcript), event_received=True)

        event_received = await self._events.wait(
            chat_id,
            after_version=version,
            timeout_seconds=timeout_seconds,
        )
        if event_received or not self._transport.live:
            transcript = await self._verified_read(
                chat_id,
                since=since,
                version=self._events.version(chat_id),
                refresh_participants=event_received,
            )
            return RoomEvent(**dict(transcript), event_received=event_received)

        quiet = self._pulses[chat_id].snapshot.model_copy(
            update={
                "messages": [],
                "pending_requests": [],
                "next_since": max(after or EPOCH, started),
                "refreshed_at": datetime.now(timezone.utc),
                "transport": self._transport,
                "host": self.host,
            }
        )
        return RoomEvent(**dict(quiet), event_received=False)

    def _read_is_current(
        self,
        chat_id: str,
        version: int,
        after: datetime | None,
    ) -> bool:
        """Whether the last read provably still answers this caller."""
        pulse = self._pulses.get(chat_id)
        return (
            pulse is not None
            and self._transport.live
            and version == pulse.version
            and after is not None
            and after >= pulse.newest_message_at
        )

    async def _verified_read(
        self,
        chat_id: str,
        *,
        since: str | None,
        version: int,
        refresh_participants: bool = False,
    ) -> RoomTranscript:
        """Read, and record the proof a later tick needs to skip reading."""
        transcript = await self.read(
            chat_id,
            since=since,
            refresh_participants=refresh_participants,
        )
        previous = self._pulses.get(chat_id)
        newest = max(
            [message.at for message in transcript.messages]
            + [previous.newest_message_at if previous else EPOCH]
        )
        self._pulses[chat_id] = ReadPulse(version, newest, transcript)
        return transcript
