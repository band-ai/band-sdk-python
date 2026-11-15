"""Priority controls require shared read-only projection and recognition."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.runtime.batching.kit import (
    ControlHandlingAdapter,
    LegacyAdapter,
    RecognizingAdapter,
    RewritingPreprocessor,
    classify,
)


class TestControlRecognition:
    async def test_an_adapter_without_a_control_handler_has_no_controls(
        self, ctx: MagicMock
    ) -> None:
        assert await classify(LegacyAdapter(), ctx, "approve") is False

    async def test_a_handler_without_a_recognizer_keeps_ordinary_order(
        self, ctx: MagicMock
    ) -> None:
        assert await classify(ControlHandlingAdapter(), ctx, "hello") is False

    async def test_a_recognizer_decides_from_the_message_it_is_given(
        self, ctx: MagicMock
    ) -> None:
        adapter = RecognizingAdapter()

        assert [
            await classify(adapter, ctx, text) for text in ("approve", "hello")
        ] == [
            True,
            False,
        ]

    async def test_a_custom_projection_classifies_the_transformed_message(
        self, ctx: MagicMock
    ) -> None:
        verdict = await classify(
            RecognizingAdapter(), ctx, "hello", preprocessor=RewritingPreprocessor()
        )

        assert verdict is True
