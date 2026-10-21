"""What CrewAI remembers of a batch turn, as its next turn's prompt shows it."""

from __future__ import annotations

import pytest

from band.adapters.crewai import CURRENT_INPUT_LABEL
from tests.framework_conformance.batchprobes import BATCH, EARLIER_QUESTION, FINAL_TEXT
from tests.framework_conformance.batchprobes.crewai import recording_adapter
from tests.framework_conformance.batchprobes.laterturn import (
    deliver_batch_then_next_turn,
)


def earlier_user_lines(prompt: str) -> list[str]:
    """The user lines of the earlier conversation a prompt carries."""
    earlier, _, _ = prompt.partition(CURRENT_INPUT_LABEL)
    return [line for line in earlier.splitlines() if line.startswith("user: ")]


class TestCrewAIBatchHistory:
    async def test_the_next_turn_remembers_each_original_once_in_order(self) -> None:
        pytest.importorskip("crewai")
        adapter, model = await recording_adapter(FINAL_TEXT)

        await deliver_batch_then_next_turn(adapter)

        assert earlier_user_lines(model.received[-1]["content"]) == [
            f"user: [Alice]: {EARLIER_QUESTION}",
            *(f"user: [{o.sender_name}]: {o.content}" for o in BATCH),
        ]
