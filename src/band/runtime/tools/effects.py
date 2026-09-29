"""What a finished tool call does to the turn's reply obligation.

One ``TurnEffect`` per tool answers both questions an adapter asks after a call:
did the turn do work (``is_terminal_success``: an empty final answer is then
benign) and is its reply settled (``settles_turn_reply``: no fallback text may be
relayed).
"""

from __future__ import annotations

from collections.abc import Mapping

from band.runtime.tools.registry import (
    ALL_TOOL_NAMES,
    EVENT_TOOL_NAMES,
    LEGACY_SEND_MESSAGE_TOOL,
    READ_ONLY_TOOL_NAMES,
    canonicalize_mcp_tool_name,
)
from band.runtime.tools.types import BandTool, TurnEffect

# Every Band tool not listed below is a durable action (ACT).
# ``band_send_room_file`` also posts a message (the file's attaching message).
_BAND_EFFECTS: dict[str, TurnEffect] = {
    **dict.fromkeys(ALL_TOOL_NAMES, TurnEffect.ACT),
    **dict.fromkeys(READ_ONLY_TOOL_NAMES | EVENT_TOOL_NAMES, TurnEffect.OBSERVE),
    **dict.fromkeys(
        {BandTool.SEND_MESSAGE, BandTool.SEND_ROOM_FILE, LEGACY_SEND_MESSAGE_TOOL},
        TurnEffect.REPLY,
    ),
    BandTool.NO_REPLY: TurnEffect.DECLINE,
}


def turn_effect(
    tool_name: str, *, custom_effects: Mapping[str, TurnEffect] | None = None
) -> TurnEffect:
    """The turn effect of a tool. A tool that declared none is ``OBSERVE``.

    A Band tool is resolved first, tolerating the Band MCP server's own ``band-``
    spelling (see ``canonicalize_mcp_tool_name``) but nothing else, so a custom tool
    cannot redefine a Band tool and an unrelated MCP server's tool that merely ends
    in ``-band_send_message`` never resolves. Any other name is looked up in
    ``custom_effects``: the effects the caller's own tools declared (see
    ``runtime.custom_tools.declares_turn_effect``).
    """
    band_tool = canonicalize_mcp_tool_name(tool_name, _BAND_EFFECTS)
    if band_tool in _BAND_EFFECTS:
        return _BAND_EFFECTS[band_tool]
    return (custom_effects or {}).get(tool_name, TurnEffect.OBSERVE)


def settles_turn_reply(
    tool_name: str, *, custom_effects: Mapping[str, TurnEffect] | None = None
) -> bool:
    """Whether a successful call of ``tool_name`` is the turn's reply.

    True for a room post or ``band_no_reply``'s deliberate silence, and for a
    custom tool that declared either. Bridge adapters relay the model's plain text
    only when no call settled the reply, so the reply is delivered exactly once. A
    miss only costs a duplicate reply (the pre-suppression behavior), never a
    wrong post.
    """
    return turn_effect(tool_name, custom_effects=custom_effects).settles_reply


def is_terminal_success(
    tool_name: str | None,
    *,
    succeeded: bool,
    custom_effects: Mapping[str, TurnEffect] | None = None,
) -> bool:
    """Whether a finished tool call counts as terminal productive work.

    Single source of truth shared by the tool-only adapters (claude_sdk, crewai,
    pydantic-ai, strands) to decide whether an empty final model response is
    *benign* (the agent already did its work this turn) or a genuine no-response
    failure. Terminal work is a call that did not fail and whose ``turn_effect``
    did work.

    Observing tools never count -- fetching state (``READ_ONLY_TOOL_NAMES``) or
    narrating (``EVENT_TOOL_NAMES``: band_send_event posts a thought/error/task
    event) is not a chat reply or a durable requested action. Custom tools are
    **not** terminal by default: the SDK cannot know whether a bare custom tool is a
    lookup or a side-effecting action, so it fails loud — an empty final after only
    an undeclared custom tool surfaces as a no-response error rather than being
    silently swallowed. A custom tool that genuinely completes the turn declares
    its effect (see ``runtime.custom_tools.declares_turn_effect``).
    """
    return (
        succeeded
        and tool_name is not None
        and turn_effect(tool_name, custom_effects=custom_effects).did_work
    )
