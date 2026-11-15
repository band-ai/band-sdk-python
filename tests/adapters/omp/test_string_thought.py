"""Closing assistant text follows thought visibility and reply ownership."""

from __future__ import annotations

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit, MessageType
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpEvent,
    OmpEventType,
    OmpMessage,
)
from band.testing.fake_tools import events_of_type
from tests.adapters.omp.turns import NativeTurnFactory, reply_call


class TestStringThought:
    @pytest.mark.parametrize(
        ("role", "content", "expected"),
        [
            (OMP_ASSISTANT_ROLE, "Visible assistant text", ["Visible assistant text"]),
            (OMP_ASSISTANT_ROLE, "   \n", []),
            ("user", "Visible assistant text", []),
        ],
        ids=["assistant", "whitespace", "non-assistant"],
    )
    async def test_only_visible_assistant_content_is_posted_as_thought(
        self,
        native_turn: NativeTurnFactory,
        role: str,
        content: str,
        expected: list[str],
    ) -> None:
        with pytest.raises(TurnResultAlreadyReported):
            async with native_turn(emit={Emit.THOUGHTS}) as turn:
                await turn.callbacks.event(
                    OmpEvent(
                        type=OmpEventType.MESSAGE_END,
                        message=OmpMessage(role=role, content=content),
                    )
                )
        assert [
            event["content"]
            for event in events_of_type(turn.tools, MessageType.THOUGHT)
        ] == expected

    async def test_closing_thought_after_reply_is_not_posted(
        self, native_turn: NativeTurnFactory
    ) -> None:
        async with native_turn(emit={Emit.THOUGHTS}) as turn:
            await turn.callbacks.tool(reply_call("Already replied"))
            await turn.callbacks.event(
                OmpEvent(
                    type=OmpEventType.MESSAGE_END,
                    message=OmpMessage(
                        role=OMP_ASSISTANT_ROLE, content="Closing thought after reply"
                    ),
                )
            )
        assert events_of_type(turn.tools, MessageType.THOUGHT) == []
