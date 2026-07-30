"""Transcript reads for the Desktop room view, proven current by the relay."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from band.client.rest import DEFAULT_REQUEST_OPTIONS, AsyncRestClient, ChatRoomRequest
from band.integrations.desktop_app.event_relay import RelayStatus, RoomEventBroker
from band.integrations.desktop_app.room import (
    EPOCH,
    AgentIdentity,
    HostProfile,
    MonitoringStatus,
    RoomEvent,
    RoomMessage,
    RoomParticipant,
    RoomTranscript,
    parse_timestamp,
)
from band.integrations.desktop_app.prompts import (
    ambiguous_room_guidance,
    monitoring_notice,
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

# How long after its wait should have returned the agent may take to call
# again before its loop is reported stopped rather than busy. An iteration
# costs one whole quantum plus whatever the agent does with what it got, and
# that work does not scale with the quantum — so this is added to the agent's
# own wait, not multiplied by it. A multiple was both too slow on a long
# quantum (90s unwatched) and too tight on a short one (15s, less than a
# single answer in the room takes).
STALE_GRACE_S = 30

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
class ModelTick:
    """When the agent last monitored, and the quantum it chose to wait.

    The quantum is the model's to pick per call — the briefing has it use 5
    seconds while its user is talking and the install default once quiet — so
    a limit read off the install default would call a healthy long-quantum loop
    stopped after a single wait.
    """

    at: datetime
    quantum: float


@dataclass
class ReadPulse:
    """What the last REST read of a room saw.

    ``newest_message_at`` is the newest message any read of this room has ever
    returned — the watermark a caller may be behind — and ``snapshot`` the
    transcript, kept for re-offering wakes. Deliberately not a proof that a
    later tick may skip reading: the platform does not echo an agent's own
    messages back to its own WebSocket, so a message the agent posts through
    band-mcp produces no event, and any "the room is provably unchanged"
    argument built on the event stream silently loses exactly those messages.
    """

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
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._tools = tools
        self._viewer = viewer
        self._events = events
        self._transport = transport or RelayStatus()
        self.tuning = tuning or RoomViewTuning()
        self._now = now
        self._participants: dict[str, list[RoomParticipant]] = {}
        self._announced_rooms: set[str] = set()
        self._pulses: dict[str, ReadPulse] = {}
        self._model_ticks: dict[str, ModelTick] = {}
        self._reported_stale: set[str] = set()
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

    def note_model_tick(self, chat_id: str, *, quantum: float) -> None:
        """Record that the agent's own monitor loop is still running."""
        self._model_ticks[chat_id] = ModelTick(self._now(), quantum)

    def monitoring(self, chat_id: str) -> MonitoringStatus:
        """How long since the agent last monitored this room, and whether that
        gap means its loop stopped.

        Unknown until the agent has monitored once: before that the join
        summary is already telling it to start, and repeating it would say
        nothing new.
        """
        tick = self._model_ticks.get(chat_id)
        if tick is None:
            return MonitoringStatus()
        idle = (self._now() - tick.at).total_seconds()
        return MonitoringStatus(
            idle_seconds=idle,
            stale=idle > tick.quantum + STALE_GRACE_S,
        )

    def claim_stale_report(self, chat_id: str, monitoring: MonitoringStatus) -> bool:
        """Whether this is the first stale reading since the loop last ran.

        The view keeps ticking whatever the agent does, so a stopped loop is
        seen again every few seconds; reporting each one would bury the log it
        is meant to be found in.
        """
        if not monitoring.stale:
            self._reported_stale.discard(chat_id)
            return False
        if chat_id in self._reported_stale:
            return False
        self._reported_stale.add(chat_id)
        return True

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
        started_at = self._now()
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
        transcript.monitoring = self.monitoring(chat_id)
        transcript.monitoring_notice = monitoring_notice(transcript.monitoring)
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

        Every tick ends in a REST read. An event ends the wait early; a quiet
        wait reads anyway, because the event stream is not complete: the
        platform does not echo the agent's own messages back to its own
        socket, so posts made through band-mcp arrive only by reading. A
        caller already behind the newest message ever read is answered before
        waiting at all.
        """
        if self._events is None:
            raise RuntimeError("Room WebSocket events are not configured.")

        version = self._events.version(chat_id)
        after = parse_timestamp(since)

        # A caller is answered before waiting unless it is provably caught up
        # with the newest message ever read: a first call, or one resuming an
        # old cursor, is owed its backlog now, not after a quantum of silence.
        pulse = self._pulses.get(chat_id)
        behind = pulse is None or after is None or after < pulse.newest_message_at
        if behind:
            transcript = await self._verified_read(chat_id, since=since)
            if transcript.messages:
                return RoomEvent(**dict(transcript), event_received=True)

        event_received = await self._events.wait(
            chat_id,
            after_version=version,
            timeout_seconds=timeout_seconds,
        )
        transcript = await self._verified_read(
            chat_id,
            since=since,
            refresh_participants=event_received,
        )
        return RoomEvent(**dict(transcript), event_received=event_received)

    async def _verified_read(
        self,
        chat_id: str,
        *,
        since: str | None,
        refresh_participants: bool = False,
    ) -> RoomTranscript:
        """Read, and record the watermark and snapshot of what was seen."""
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
        self._pulses[chat_id] = ReadPulse(newest, transcript)
        return transcript
