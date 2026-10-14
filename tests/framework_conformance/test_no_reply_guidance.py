"""Every prompt and schema surface carries band-sdk-core's whole-turn decline rule."""

from __future__ import annotations

import pytest
from band_sdk_core import no_reply_tool

from band.integrations.claude_sdk.prompts import generate_claude_sdk_agent_prompt
from band.runtime.prompts import (
    COMMUNICATION_INSTRUCTIONS,
    TURN_COMPLETION_GUIDANCE,
    render_system_prompt,
)
from band.runtime.tools import BandTool, get_tool_description

CANONICAL_DESCRIPTION = no_reply_tool()["description"]

ADVERTISED_GUIDANCE = {
    "base system prompt": lambda: render_system_prompt(),
    "codex and acp communication instructions": lambda: COMMUNICATION_INSTRUCTIONS,
    "claude sdk system prompt": lambda: generate_claude_sdk_agent_prompt("Agent")[
        "append"
    ],
}


class TestNoReplyGuidance:
    """The decline rule is one core text, judged over the whole turn."""

    @pytest.mark.parametrize("surface", ADVERTISED_GUIDANCE)
    def test_prompt_surfaces_carry_the_canonical_description(
        self, surface: str
    ) -> None:
        assert CANONICAL_DESCRIPTION in ADVERTISED_GUIDANCE[surface]()

    def test_the_advertised_tool_description_is_the_canonical_text(self) -> None:
        assert get_tool_description(BandTool.NO_REPLY) == CANONICAL_DESCRIPTION

    @pytest.mark.parametrize("surface", ADVERTISED_GUIDANCE)
    def test_no_surface_judges_only_the_latest_message(self, surface: str) -> None:
        assert "latest message" not in ADVERTISED_GUIDANCE[surface]()

    def test_send_message_does_not_restate_the_decline_rule(self) -> None:
        description = get_tool_description(BandTool.SEND_MESSAGE)

        assert BandTool.NO_REPLY not in description

    def test_turn_completion_refers_to_the_current_inputs(self) -> None:
        assert "current inputs" in TURN_COMPLETION_GUIDANCE
