"""Unit tests for the shared "terminal productive work" predicate.

``is_terminal_success`` is the single source of truth the tool-only adapters use
to decide whether an empty final model response is *benign* (the agent already
did its work) or a genuine no-response failure. The fail-loud policy: read-only
Band tools and *undeclared* custom tools never count; a custom tool must declare
its effect (``declares_turn_effect``, or the ``band_terminal`` shorthand).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.custom_tools import declared_effect, declares_turn_effect
from band.runtime.tools import (
    ALL_TOOL_NAMES,
    AgentTools,
    BandTool,
    TurnEffect,
    band_tool_errored,
    is_terminal_success,
    settles_turn_reply,
)


def test_failed_tool_never_counts() -> None:
    assert is_terminal_success(BandTool.SEND_MESSAGE, succeeded=False) is False


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
    ("tool", "custom_effects", "did_work", "settles_reply"),
    [
        pytest.param(BandTool.SEND_MESSAGE, None, True, True, id="post"),
        pytest.param(BandTool.NO_REPLY, None, True, True, id="no-reply"),
        # Silence after an action is benign, yet the model's plain text is still owed.
        pytest.param(BandTool.ADD_PARTICIPANT, None, True, False, id="action"),
        pytest.param(BandTool.GET_MEMORY, None, False, False, id="read-only"),
        pytest.param(BandTool.SEND_EVENT, None, False, False, id="narration"),
        # An undeclared custom tool fails loud: no work, the reply still owed.
        pytest.param("weather", _POST_TO_SLACK, False, False, id="undeclared"),
        pytest.param("post_to_slack", _POST_TO_SLACK, True, False, id="declared-act"),
        pytest.param("stay_quiet", _STAY_QUIET, True, True, id="declared-silence"),
    ],
)
def test_the_two_views_of_a_finished_tool_call(
    tool: str,
    custom_effects: dict[str, TurnEffect] | None,
    did_work: bool,
    settles_reply: bool,
) -> None:
    assert (
        is_terminal_success(tool, succeeded=True, custom_effects=custom_effects)
        is did_work
    )
    assert settles_turn_reply(tool, custom_effects=custom_effects) is settles_reply


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
    assert settles_turn_reply(BandTool.SEND_MESSAGE, custom_effects=hijack) is True


def test_no_reply_is_recognised_under_the_band_mcp_prefix_only() -> None:
    assert settles_turn_reply("band-band_no_reply") is True
    assert settles_turn_reply("other-band_no_reply") is False


def test_only_posts_and_no_reply_settle_the_reply() -> None:
    settling = {name for name in ALL_TOOL_NAMES if settles_turn_reply(name)}
    assert settling == {
        BandTool.SEND_MESSAGE,
        BandTool.SEND_ROOM_FILE,
        BandTool.NO_REPLY,
    }


async def test_no_reply_is_local_only(mock_rest_client: MagicMock) -> None:
    tools = AgentTools("room-1", mock_rest_client)

    outcome = await tools.execute_tool_call_structured(
        BandTool.NO_REPLY, {"reason": "addressed to another agent"}
    )

    assert (outcome.ok, outcome.value) == (True, {"status": "no_reply"})
    assert mock_rest_client.mock_calls == []
