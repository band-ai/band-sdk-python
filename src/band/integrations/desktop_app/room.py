"""Typed Band room state shared by the Desktop transcript service and its view."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from band.core.types import MessageType
from band.integrations.desktop_app.event_relay import RelayStatus
from band.integrations.desktop_app.tools import DEFAULT_ATTENTION, AttentionMode
from band.runtime.formatters import replace_uuid_mentions

EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


def bare_handle(value: str | None) -> str:
    """Normalize a Band handle for one ``@`` prefix."""
    return (value or "").lstrip("@")


def parse_timestamp(value: str | None) -> datetime | None:
    """Validate the room-view timestamp, treating naive values as UTC.

    Only ever fed the resume cursor this module itself emits (`resume_token`
    below), so stdlib parsing of that self-generated ISO 8601 shape is enough.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class RoomParticipantType(StrEnum):
    """Known participant types returned by the room API."""

    USER = "user"
    AGENT = "agent"


class AgentIdentity(BaseModel):
    """The Band agent Claude Desktop acts as, from ``/api/v1/agent/me``."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    name: str | None = None
    handle: str | None = None
    description: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.bare_handle or self.id or "the connected Band agent"

    @property
    def bare_handle(self) -> str:
        return bare_handle(self.handle)


class RoomParticipant(BaseModel):
    """One member of a Band room, as the agent participants API reports it."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    name: str | None = None
    handle: str | None = None
    type: str | None = None
    role: str | None = None

    @property
    def is_human(self) -> bool:
        return (self.type or "").casefold() == RoomParticipantType.USER

    @property
    def bare_handle(self) -> str:
        """The handle suitable for one normalized ``@`` prefix."""
        return bare_handle(self.handle)

    def describe(self) -> str:
        """A one-line introduction for the briefing Claude reads."""
        traits = [
            trait
            for trait in (
                f"@{self.bare_handle}" if self.bare_handle else "",
                (self.type or "").casefold(),
                (self.role or "").casefold(),
            )
            if trait
        ]
        label = self.name or self.bare_handle or self.id or "unknown"
        return f"{label} ({', '.join(traits)})" if traits else label


class RoomMessage(BaseModel):
    """One agent-visible room message."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    content: str = ""
    sender_id: str = ""
    sender_type: str = ""
    sender_name: str | None = None
    message_type: str = MessageType.TEXT
    metadata: dict[str, Any] = Field(default_factory=dict)
    inserted_at: datetime | None = None
    addressed_to_viewer: bool = False

    @field_validator("id", "content", "sender_id", "sender_type", mode="before")
    @classmethod
    def _blank_when_absent(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("message_type", mode="before")
    @classmethod
    def _text_when_absent(cls, value: Any) -> Any:
        return value or MessageType.TEXT

    @field_validator("metadata", mode="before")
    @classmethod
    def _empty_when_absent(cls, value: Any) -> Any:
        return value or {}

    @field_validator("inserted_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)

    @property
    def at(self) -> datetime:
        """The send instant, ordering undated messages first."""
        return self.inserted_at or EPOCH

    @property
    def is_text(self) -> bool:
        return self.message_type.casefold() == MessageType.TEXT

    def addresses(self, viewer: AgentIdentity) -> bool:
        """Whether this message explicitly mentions the connected agent."""
        handle = viewer.bare_handle.casefold()
        for mention in self.metadata.get("mentions") or []:
            if not isinstance(mention, dict):
                continue
            if viewer.id and str(mention.get("id") or "") == viewer.id:
                return True
            mentioned = bare_handle(
                str(mention.get("handle") or mention.get("username") or "")
            ).casefold()
            if handle and mentioned == handle:
                return True
        return bool(viewer.id and f"@[[{viewer.id}]]" in self.content)

    def truncate(self, limit: int) -> None:
        if len(self.content) > limit:
            self.content = f"{self.content[:limit]}… [truncated]"

    def render_mentions(self, participants: list[RoomParticipant]) -> None:
        """Rewrite Band's stored ``@[[id]]`` markers as readable ``@handle``.

        Every other adapter does this through the same helper. Skipping it here
        let the agent read the stored form as mention syntax and write
        ``@[[handle]]`` back into its own messages, which Band cannot resolve
        and renders as literal text.
        """
        self.content = replace_uuid_mentions(
            self.content,
            [participant.model_dump() for participant in participants],
        )


class HostProfile(BaseModel):
    """What the MCP host declared about itself when it connected.

    Recorded because the host's capabilities decide which designs are even
    possible here: `sampling` would let this server start a Claude turn when a
    room event arrives, instead of Claude having to hold a turn open waiting.
    """

    captured: bool = False
    name: str = ""
    version: str = ""
    sampling: bool = False
    elicitation: bool = False
    roots: bool = False
    tasks: bool = False
    experimental: list[str] = Field(default_factory=list)

    @classmethod
    def from_client_params(cls, params: Any) -> HostProfile:
        """Read what an MCP host declared in its initialize params."""
        capabilities = params.capabilities
        return cls(
            captured=True,
            name=params.clientInfo.name,
            version=params.clientInfo.version,
            sampling=capabilities.sampling is not None,
            elicitation=capabilities.elicitation is not None,
            roots=capabilities.roots is not None,
            tasks=capabilities.tasks is not None,
            experimental=sorted(capabilities.experimental or {}),
        )

    @property
    def can_be_woken(self) -> bool:
        """Whether the host lets this server initiate a model turn."""
        return self.sampling


class MonitoringStatus(BaseModel):
    """Whether the agent's own monitor loop is still running.

    The view's display loop keeps ticking whatever the agent does, so nothing
    the agent can see distinguishes a watched room from one it stopped watching
    after answering its user. This is that fact, stated.
    """

    idle_seconds: float | None = None
    stale: bool = False

    @property
    def idle_for(self) -> str:
        """How long since the agent's last monitor call, in words."""
        seconds = int(self.idle_seconds or 0)
        return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"


class RoomTranscript(BaseModel):
    """One agent-visible view of a Band room at a point in time."""

    chat_id: str
    viewer: AgentIdentity
    participants: list[RoomParticipant] = Field(default_factory=list)
    messages: list[RoomMessage] = Field(default_factory=list)
    pending_requests: list[RoomMessage] = Field(default_factory=list)
    role_briefing: str = ""
    monitoring_notice: str = ""
    attention: AttentionMode = DEFAULT_ATTENTION
    next_since: datetime = EPOCH
    transport: RelayStatus = Field(default_factory=RelayStatus)
    monitoring: MonitoringStatus = Field(default_factory=MonitoringStatus)
    host: HostProfile = Field(default_factory=HostProfile)
    refreshed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @property
    def resume_token(self) -> str:
        """`next_since` spelled exactly as the JSON payload spells it."""
        return self.next_since.isoformat().replace("+00:00", "Z")

    @property
    def peers(self) -> list[RoomParticipant]:
        """Everyone in the room except the agent itself."""
        return [item for item in self.participants if item.id != self.viewer.id]

    @property
    def humans(self) -> list[RoomParticipant]:
        return [item for item in self.peers if item.is_human]


class RoomEvent(RoomTranscript):
    """A transcript delivered because the room's WebSocket reported a change."""

    event_received: bool
    # True when a newer view instance has taken over this room's display: the
    # receiving instance collapses itself instead of living on as a duplicate.
    superseded: bool = False

    def tick(self) -> RoomEvent:
        """This event stripped of everything the caller already holds.

        A quiet tick repeats every few seconds for as long as the agent is
        monitoring, so re-sending the roster and briefing on each one is pure
        cost to read. The monitoring notice stays: it is the one thing a caller
        holding an older tick cannot already know.
        """
        return self.model_copy(update={"participants": [], "role_briefing": ""})
