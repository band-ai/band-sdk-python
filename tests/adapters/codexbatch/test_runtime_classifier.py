"""The runtime classifies a delivery with the adapter's recognizer, touching nothing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.adapters.codex import CodexAdapter
from band.agent import Agent
from tests.conftest import make_message_event
from tests.runtime.batching.kit import room_context
from tests.runtime.helpers import AGENT_ID


def is_control(ctx: MagicMock, content: str) -> bool:
    agent = Agent(runtime=MagicMock(agent_id=AGENT_ID), adapter=CodexAdapter())
    return agent._is_control(ctx, make_message_event(content=content))


class TestRuntimeClassifier:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("/approve req-1", True),
            ("@bob /decline req-1", True),
            ("/model gpt-5", True),
            ("run the tests", False),
            ("please /approve it", False),
        ],
    )
    def test_a_delivery_is_a_control_exactly_when_it_is_a_local_command(
        self, content: str, expected: bool
    ) -> None:
        assert is_control(room_context(), content) is expected

    def test_classifying_leaves_the_room_context_unconsumed(self) -> None:
        ctx = room_context()

        is_control(ctx, "/approve req-1")

        assert ctx.mock_calls == []
