"""System prompts and tool schemas carry the shared recipient guidance."""

from __future__ import annotations

import pytest

from band.runtime.prompts import COMMUNICATION_INSTRUCTIONS, render_system_prompt
from band.runtime.tools.inputs.chat import MENTION_GUIDANCE, SendMessageInput


class TestMentionGuidance:
    @pytest.mark.parametrize(
        "text",
        [
            COMMUNICATION_INSTRUCTIONS,
            render_system_prompt(),
            SendMessageInput.model_json_schema()["properties"]["mentions"][
                "description"
            ],
        ],
        ids=["communication", "system-prompt", "tool-schema"],
    )
    def test_model_receives_shared_guidance(self, text: str) -> None:
        assert MENTION_GUIDANCE in text
