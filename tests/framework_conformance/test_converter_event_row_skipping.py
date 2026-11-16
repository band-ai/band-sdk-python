"""Every history converter leaves thought and error rows out of the model's history."""

from __future__ import annotations

import pytest

from band.core.types import MessageType


class TestEventRowSkipping:
    @pytest.mark.parametrize(
        ("message_type", "kept"),
        [
            (MessageType.TEXT, True),
            (MessageType.THOUGHT, False),
            (MessageType.ERROR, False),
        ],
    )
    def test_keeps_another_agents_row_only_when_it_is_text(
        self, make_converter, output, message_type: MessageType, kept: bool
    ):
        """The text control proves only the row's type decides whether it drops."""
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

        assert output.is_empty(result) is not kept
