from __future__ import annotations

from pydantic import BaseModel

from band.testing.activity import ActivityRecorder
from tests.adapters.codex.kit import ToolSchemaFakeTools, run_codex_turn
from tests.adapters.codexturns import final_text, tool_call_request, turn_completed


class LookupInput(BaseModel):
    """Look up a word."""

    word: str


def lookup(inp: LookupInput) -> str:
    return f"{inp.word}: a definition"


class TestCodexCustomToolActivity:
    async def test_a_custom_tool_shows_its_name_until_it_returns(
        self, recorder: ActivityRecorder
    ) -> None:
        await run_codex_turn(
            events=[
                tool_call_request(1, "lookup", {"word": "activity"}),
                final_text("Done."),
                turn_completed(),
            ],
            tools=recorder.observing(ToolSchemaFakeTools()),
            additional_tools=[(LookupInput, lookup)],
        )

        recorder.assert_completed_tool("lookup")
