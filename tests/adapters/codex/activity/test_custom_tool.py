from __future__ import annotations

import asyncio

from band.testing.activity import ActivityRecorder, shown_tool
from tests.adapters.codex.kit import ToolSchemaFakeTools, run_codex_turn
from tests.adapters.codexturns import final_text, tool_call_request, turn_completed
from tests.integrations.activitykit import LookupInput


class TestCodexCustomToolActivity:
    async def test_a_custom_tool_shows_its_name_until_it_returns(
        self, recorder: ActivityRecorder
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def lookup(inp: LookupInput) -> str:
            entered.set()
            await release.wait()
            return f"{inp.note}: a definition"

        turn = asyncio.create_task(
            run_codex_turn(
                events=[
                    tool_call_request(1, "lookup", {"note": "activity"}),
                    final_text("Done."),
                    turn_completed(),
                ],
                tools=recorder.observing(ToolSchemaFakeTools()),
                additional_tools=[(LookupInput, lookup)],
                emit=(),
            )
        )
        try:
            await entered.wait()
            assert recorder.events == [shown_tool("lookup")]
        finally:
            release.set()
            finished = await turn
            await finished.adapter.cleanup_all()

        recorder.assert_completed_tool("lookup")
