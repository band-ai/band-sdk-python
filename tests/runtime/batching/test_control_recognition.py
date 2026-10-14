"""Control recognition is read-only and conservative where it cannot be exact."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from band.agent import Agent
from band.core.simple_adapter import SimpleAdapter
from band.preprocessing.default import DefaultPreprocessor
from tests.conftest import make_message_event
from tests.runtime.batching.kit import (
    AGENT_ID,
    ControlHandlingAdapter,
    LegacyAdapter,
    RecognizingAdapter,
    RewritingPreprocessor,
)


def classify(
    adapter: SimpleAdapter[Any],
    ctx: MagicMock,
    content: str,
    preprocessor: DefaultPreprocessor | None = None,
) -> bool:
    agent = Agent(
        runtime=MagicMock(agent_id=AGENT_ID),
        adapter=adapter,
        preprocessor=preprocessor,
    )
    return agent._is_control(ctx, make_message_event(content=content))


class TestControlRecognition:
    def test_an_adapter_without_a_control_handler_has_no_controls(
        self, ctx: MagicMock
    ) -> None:
        assert classify(LegacyAdapter(), ctx, "approve") is False

    def test_a_handler_without_a_recognizer_is_probed_for_every_message(
        self, ctx: MagicMock
    ) -> None:
        assert classify(ControlHandlingAdapter(), ctx, "hello") is True

    def test_a_recognizer_decides_from_the_message_it_is_given(
        self, ctx: MagicMock
    ) -> None:
        adapter = RecognizingAdapter()

        assert [classify(adapter, ctx, text) for text in ("approve", "hello")] == [
            True,
            False,
        ]

    def test_a_custom_preprocessor_is_probed_rather_than_misclassified(
        self, ctx: MagicMock
    ) -> None:
        verdict = classify(
            RecognizingAdapter(), ctx, "hello", preprocessor=RewritingPreprocessor()
        )

        assert verdict is True
