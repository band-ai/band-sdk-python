"""What a finished tool call does to the turn's reply obligation.

Every tool call records its ``TurnEffect`` on the turn's ledger
(``band.core.turn.Turn``), which band-sdk-core's rule then judges.
"""

from __future__ import annotations

from collections.abc import Mapping

from band_sdk_core import band_tool_effects

from band.runtime.tools.registry import (
    LEGACY_SEND_MESSAGE_TOOL,
    canonicalize_mcp_tool_name,
)
from band.runtime.tools.types import BandTool, TurnEffect

# band-sdk-core owns the effect of every Band tool. The legacy send-message
# spelling is Python's own alias for band_send_message.
_BAND_EFFECTS: dict[str, TurnEffect] = {
    name: TurnEffect(effect.wire_name) for name, effect in band_tool_effects().items()
}
_BAND_EFFECTS[LEGACY_SEND_MESSAGE_TOOL] = _BAND_EFFECTS[BandTool.SEND_MESSAGE]


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
