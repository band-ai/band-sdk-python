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


class AttentionMode(StrEnum):
    """Whose attention the joined room gets first.

    ROOM_FIRST holds the turn open on the monitor loop, so the room is
    answered in seconds and the user's typing waits one quantum. USER_FIRST
    inverts it: no turn is held, the user is answered instantly, and the room
    is swept once at the start of each turn — so a mention waits until the
    user next speaks or clicks the view's Check room button.
    """

    ROOM_FIRST = "room_first"
    USER_FIRST = "user_first"


ATTENTION_CHOICE = (
    "room_first (default): hold your turn open and keep monitoring; the room "
    "is answered in seconds. user_first: the user leads; you sweep the room "
    "once at the start of each turn and otherwise end turns normally. Choose "
    "user_first only when the user asks to be answered first, for on-demand "
    "or quiet room checking."
)


class MonitorCaller(StrEnum):
    """Who is driving a monitor call.

    Both loops call the same tool, so without this the server cannot tell the
    agent's own loop from the view's display loop — and the agent's is the one
    whose silence leaves the room unwatched.
    """

    MODEL = "model"
    APP = "app"


class CreateAndOpenRoomInput(BaseModel):
    """Create a Band room and open its live collaboration view."""

    task_id: str | None = Field(
        default=None,
        description="Optional Band task ID to associate with the new room.",
    )
    attention: AttentionMode = Field(
        AttentionMode.ROOM_FIRST,
        description=ATTENTION_CHOICE,
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
    attention: AttentionMode = Field(
        AttentionMode.ROOM_FIRST,
        description=ATTENTION_CHOICE,
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
    caller: MonitorCaller = Field(
        MonitorCaller.MODEL,
        description="Leave unset. The room view sets this on its display loop.",
    )
    attention: AttentionMode | None = Field(
        None,
        description=(
            "Pass only when the user asks to change how this room gets your "
            f"attention. {ATTENTION_CHOICE}"
        ),
    )
