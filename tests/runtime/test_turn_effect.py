"""What each tool's successful call does to the turn.

``turn_effect`` resolves a Band tool from band-sdk-core's table and any other
tool from the effects the caller's custom tools declared. Read-only Band tools,
narration and *undeclared* custom tools only observe; a custom tool that does
real work or answers declares it (``declares_turn_effect``, or the
``band_terminal`` shorthand).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.custom_tools import declared_effect, declares_turn_effect
from band.runtime.tools import (
    AgentTools,
    BandTool,
    TurnEffect,
    band_tool_errored,
    turn_effect,
)


def test_band_tool_errored_detects_error_prefix() -> None:
    assert band_tool_errored("band_send_message", "Error sending: nope") is True
    assert band_tool_errored("band_send_message", "sent") is False
    # Only known Band tools follow the "Error " convention; custom tools do not.
    assert band_tool_errored("lookup", "Error whatever") is False
    # Non-string content (e.g. a dict result) is not an error string.
    assert band_tool_errored("band_store_memory", {"id": "m1"}) is False
    assert band_tool_errored(None, "Error x") is False


_POST_TO_SLACK = {"post_to_slack": TurnEffect.ACT}
_STAY_QUIET = {"stay_quiet": TurnEffect.DECLINE}


@pytest.mark.parametrize(
    ("tool", "custom_effects", "effect"),
    [
        pytest.param("weather", _POST_TO_SLACK, TurnEffect.OBSERVE, id="undeclared"),
        pytest.param(
            "post_to_slack", _POST_TO_SLACK, TurnEffect.ACT, id="declared-act"
        ),
        pytest.param(
            "stay_quiet", _STAY_QUIET, TurnEffect.DECLINE, id="declared-silence"
        ),
    ],
)
def test_a_custom_tool_has_only_the_effect_it_declared(
    tool: str, custom_effects: dict[str, TurnEffect] | None, effect: TurnEffect
) -> None:
    assert turn_effect(tool, custom_effects=custom_effects) is effect


def test_declared_effect_reads_the_declaration_and_the_terminal_shorthand() -> None:
    @declares_turn_effect(TurnEffect.DECLINE)
    def stay_quiet() -> None: ...

    def terminal() -> None: ...

    terminal.band_terminal = True  # type: ignore[attr-defined]

    def plain() -> None: ...

    class TerminalModel:
        band_terminal = True

    assert declared_effect(stay_quiet) is TurnEffect.DECLINE
    assert declared_effect(terminal) is TurnEffect.ACT
    assert declared_effect(TerminalModel) is TurnEffect.ACT
    assert declared_effect(plain) is None


def test_a_custom_tool_cannot_redefine_a_band_tool() -> None:
    hijack = {BandTool.SEND_MESSAGE: TurnEffect.OBSERVE}
    assert turn_effect(BandTool.SEND_MESSAGE, custom_effects=hijack) is TurnEffect.REPLY


def test_no_reply_is_recognised_under_the_band_mcp_prefix_only() -> None:
    assert turn_effect("band-band_no_reply") is TurnEffect.DECLINE
    assert turn_effect("other-band_no_reply") is TurnEffect.OBSERVE


async def test_no_reply_is_local_only(mock_rest_client: MagicMock) -> None:
    tools = AgentTools("room-1", mock_rest_client)

    outcome = await tools.execute_tool_call_structured(
        BandTool.NO_REPLY, {"reason": "addressed to another agent"}
    )

    assert (outcome.ok, outcome.value) == (True, {"status": "no_reply"})
    assert mock_rest_client.mock_calls == []
