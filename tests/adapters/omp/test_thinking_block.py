"""Thinking blocks and closing text retain their delivery order."""

from __future__ import annotations

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit, MessageType
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContent,
    OmpContentKind,
    OmpEvent,
    OmpEventType,
    OmpMessage,
)
from band.testing.fake_tools import events_of_type
from tests.adapters.omp.turns import NativeTurnFactory


class TestThinkingBlock:
    async def test_thinking_block_is_posted_and_text_closes_as_thought(
        self, native_turn: NativeTurnFactory
    ) -> None:
        with pytest.raises(TurnResultAlreadyReported):
            async with native_turn(emit={Emit.THOUGHTS}) as turn:
                await turn.callbacks.event(
                    OmpEvent(
                        type=OmpEventType.MESSAGE_END,
                        message=OmpMessage(
                            role=OMP_ASSISTANT_ROLE,
                            content=[
                                OmpContent(
                                    type=OmpContentKind.THINKING,
                                    thinking="Internal reasoning",
                                ),
                                OmpContent(
                                    type=OmpContentKind.TEXT, text="Visible answer"
                                ),
                            ],
                        ),
                    )
                )
        assert [
            event["content"]
            for event in events_of_type(turn.tools, MessageType.THOUGHT)
        ] == ["Internal reasoning", "Visible answer"]
