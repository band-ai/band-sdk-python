"""What CrewAI remembers of a batch turn, as its next turn shows it."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

from band.adapters.crewai import CURRENT_INPUT_LABEL, CrewAIAdapter
from tests.framework_conformance.batchprobes import (
    ALICE_FIRST,
    BATCH,
    EARLIER_QUESTION,
    deliver,
    delivery_input,
)


def earlier_user_lines(prompt: str) -> list[str]:
    """The user lines of the earlier conversation a prompt carries."""
    earlier, _, _ = prompt.partition(CURRENT_INPUT_LABEL)
    return [line for line in earlier.splitlines() if line.startswith("user: ")]


class TestCrewAIBatchHistory:
    async def test_the_next_turn_remembers_each_original_once_in_order(self) -> None:
        adapter = CrewAIAdapter()
        crew = MagicMock(kickoff_async=AsyncMock(return_value=MagicMock(raw="noted")))
        adapter._crewai_agent = crew

        await deliver(adapter, await delivery_input(BATCH))
        later = replace(
            await delivery_input((ALICE_FIRST,)), is_session_bootstrap=False
        )
        await deliver(adapter, later)

        second_prompt = crew.kickoff_async.call_args_list[1].args[0]
        assert earlier_user_lines(second_prompt) == [
            f"user: [Alice]: {EARLIER_QUESTION}",
            *(f"user: [{o.sender_name}]: {o.content}" for o in BATCH),
        ]
