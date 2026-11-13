"""Every history converter leaves thought and error rows out of the model's history."""

from __future__ import annotations

import pytest

from band.core.types import MessageType


class TestEventRowSkipping:
    @pytest.mark.parametrize("message_type", [MessageType.THOUGHT, MessageType.ERROR])
    def test_skips_another_agents_event_row(
        self, make_converter, output, message_type: MessageType
    ):
        """Another agent's text row is kept, so only its type drops this one."""
        converter = make_converter()
        raw = [
            {
                "role": "assistant",
                "content": "Error: API rate limit exceeded",
                "sender_name": "Weather Agent",
                "message_type": message_type,
            }
        ]

        result = converter.convert(raw)

        assert output.is_empty(result)
