"""Data exchanged with the focused Antigravity-shaped stdio peer."""

from __future__ import annotations

from enum import StrEnum

from acp.schema import (
    PermissionOption,
    SessionConfigOptionSelect,
    StopReason,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
)
from pydantic import BaseModel, ConfigDict, Field

from tests.integrations.acp.acp_toolkit.agent import END_TURN_STOP, allow_or_reject


class PeerEvent(StrEnum):
    STARTED = "started"
    AUTHENTICATED = "authenticated"
    CREATED = "created"
    LOADED = "loaded"
    CONFIGURED = "configured"
    PROMPTED = "prompted"
    PERMISSION = "permission"


class PeerCommand(StrEnum):
    PERMISSION = "permission"


class PeerSettings(BaseModel):
    """Select external frames, not adapter decisions or expected outcomes."""

    model_config = ConfigDict(extra="forbid")

    journal_root: str | None = None
    permission: ToolCallUpdate | None = None
    permission_on_prompt: bool = True
    prompt_error: bool = False
    options: list[PermissionOption] = Field(default_factory=allow_or_reject)
    reply: str | None = "A reply from Antigravity."
    text: str | None = None
    stop_reason: StopReason = END_TURN_STOP
    config_options: list[SessionConfigOptionSelect] = Field(default_factory=list)
    narration: ToolCallStart | ToolCallProgress | None = None
    control_port: int | None = None
