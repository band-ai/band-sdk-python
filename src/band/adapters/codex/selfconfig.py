"""Tools that let Codex change its own model and reasoning."""

from __future__ import annotations

from collections.abc import Callable
from typing import get_args

from pydantic import BaseModel, Field

from band.adapters.codex.config import CodexAdapterConfig, ReasoningSummary
from band.adapters.codex.roomstate import RoomCodexClient
from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name

REASONING_SUMMARIES: frozenset[str] = frozenset(get_args(ReasoningSummary))
*_FIRST_SUMMARIES, _LAST_SUMMARY = get_args(ReasoningSummary)


class SetModelInput(BaseModel):
    """Switch the model used for subsequent turns. Call this when a different model would be more appropriate for the task (e.g. a faster model for simple queries, a stronger model for complex reasoning)."""

    model: str = Field(description="Model ID to use (e.g. 'gpt-5.5').")


class SetReasoningInput(BaseModel):
    """Adjust reasoning effort and summary detail for subsequent turns. Use higher effort for complex problems and lower effort for straightforward tasks."""

    effort: str | None = Field(
        default=None,
        description="Reasoning effort level supported by the current model (for example low, medium, or high). Omit to keep current.",
    )
    summary: str | None = Field(
        default=None,
        description=f"Reasoning summary detail: {', '.join(_FIRST_SUMMARIES)}, or {_LAST_SUMMARY}. Omit to keep current.",
    )


SET_MODEL_TOOL = get_custom_tool_name(SetModelInput)
SET_REASONING_TOOL = get_custom_tool_name(SetReasoningInput)

# Codex-local slash commands, which surface their outcome in the room themselves.
SILENT_REPORTING_TOOLS: frozenset[str] = frozenset({SET_MODEL_TOOL, SET_REASONING_TOOL})


def self_config_tools(
    active_room: Callable[[], RoomCodexClient], config: CodexAdapterConfig
) -> list[CustomToolDef]:
    """Tools that change the active room's model and reasoning for later turns.

    They run only while a tool call holds the room's ``rpc_lock``, so they
    are sync and cannot ask Codex whether a value is supported: an
    unsupported one surfaces on the next turn instead.
    """

    def locked_room(tool: str) -> RoomCodexClient:
        room = active_room()
        if not room.rpc_lock.locked():
            raise RuntimeError(f"{tool} must run under the room's rpc_lock")
        return room

    def set_model(inp: SetModelInput) -> str:
        locked_room(SET_MODEL_TOOL).selected_model = inp.model
        return f"Model changed to {inp.model} for subsequent turns."

    def set_reasoning(inp: SetReasoningInput) -> str:
        room = locked_room(SET_REASONING_TOOL)
        parts: list[str] = []
        if inp.effort is not None:
            room.reasoning_effort = inp.effort
            parts.append(f"effort={inp.effort}")
        if inp.summary is not None:
            if inp.summary not in REASONING_SUMMARIES:
                return (
                    f"Invalid reasoning summary '{inp.summary}'. "
                    f"Valid: {', '.join(sorted(REASONING_SUMMARIES))}."
                )
            room.reasoning_summary = inp.summary
            parts.append(f"summary={inp.summary}")
        if not parts:
            return (
                f"No changes. Current: effort={config.reasoning_effort or 'default'}, "
                f"summary={config.reasoning_summary or 'default'}."
            )
        return f"Reasoning updated: {', '.join(parts)}."

    return [(SetModelInput, set_model), (SetReasoningInput, set_reasoning)]
