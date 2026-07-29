"""The tool vocabulary this server exposes: names and input contracts.

Argument text lives beside its field so the schema reads as one contract.
Everything else the model is told — tool descriptions, the briefing, the
summaries — is in `prompts.py`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from band.integrations.desktop_app.settings import MAX_ROOM_EVENT_TIMEOUT_S


class RoomTool(StrEnum):
    """The tools this server exposes, named once for every reader of them."""

    JOIN = "band_join_room"
    CREATE = "band_create_and_open_room"
    REFRESH = "band_refresh_room_view"
    MONITOR = "band_wait_for_room_event"


class CreateAndOpenRoomInput(BaseModel):
    """Create a Band room and open its live collaboration view."""

    task_id: str | None = Field(
        default=None,
        description="Optional Band task ID to associate with the new room.",
    )


class JoinRoomInput(BaseModel):
    """Join a Band room as the connected agent."""

    chat_id: str = Field(
        description=(
            "Band chat room ID, or a room name/title to resolve. Pass "
            "whatever the user said; an unknown name errors with the real "
            "room list to offer back."
        )
    )


class RefreshRoomInput(BaseModel):
    """Fetch messages added to an open Band room transcript."""

    chat_id: str = Field(description="Band chat room ID.")
    since: str | None = Field(
        None,
        description="Newest ISO 8601 message timestamp already displayed.",
    )


class WaitForRoomEventInput(BaseModel):
    """Wait for the next SDK WebSocket event in an open room."""

    chat_id: str = Field(description="Band chat room ID.")
    since: str | None = Field(
        None,
        description=(
            "The `next_since` value from the previous result. Omit only on the "
            "first call. It advances every call, so passing it back both "
            "resumes exactly where you left off and keeps successive monitoring "
            "calls distinct."
        ),
    )
    timeout_seconds: int | None = Field(
        None,
        ge=1,
        le=MAX_ROOM_EVENT_TIMEOUT_S,
        description=(
            "Maximum seconds to block before a reconnect-safety refresh. Omit "
            "to use this install's configured default. Keep it short while the "
            "user is talking to you: it is also how long they wait to be heard."
        ),
    )
    retry_wakes: list[str] = Field(
        default_factory=list,
        description="Message IDs whose earlier wake the host refused or lost.",
    )
