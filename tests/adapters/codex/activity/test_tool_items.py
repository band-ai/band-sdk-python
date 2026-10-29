from __future__ import annotations

import pytest

from band.core.types import Emit
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.adapters.codex.activity.kit import (
    BAND_TOOL_CALL,
    DOCS_LOOKUP,
    LIST_FILES,
    item_completed,
    item_started,
)
from tests.adapters.codex.kit import ToolSchemaFakeTools, run_codex_turn
from tests.adapters.codexturns import final_text, turn_completed

NARRATION = pytest.mark.parametrize(
    "emit", [(Emit.TOOL_CALLS,), ()], ids=["narration-on", "narration-off"]
)


class TestCodexToolItemActivity:
    @NARRATION
    async def test_each_tool_item_shows_its_name_until_it_completes(
        self, recorder: ActivityRecorder, emit: tuple[Emit, ...]
    ) -> None:
        """A Band tool Codex asks the adapter to run reports where it runs,
        so its dynamicToolCall item shows nothing."""
        await run_codex_turn(
            events=[
                item_started(LIST_FILES),
                item_completed(LIST_FILES),
                item_started(BAND_TOOL_CALL),
                item_completed(BAND_TOOL_CALL),
                item_started(DOCS_LOOKUP),
                item_completed(DOCS_LOOKUP),
                final_text("Done."),
                turn_completed(),
            ],
            tools=recorder.observing(ToolSchemaFakeTools()),
            emit=emit,
        )

        assert recorder.events == [
            shown_tool("exec"),
            SHOWN_REASONING,
            shown_tool("mcp:docs/search"),
            SHOWN_REASONING,
        ]

    @NARRATION
    async def test_the_older_item_shows_again_when_the_newer_completes_first(
        self, recorder: ActivityRecorder, emit: tuple[Emit, ...]
    ) -> None:
        await run_codex_turn(
            events=[
                item_started(LIST_FILES),
                item_started(DOCS_LOOKUP),
                item_completed(DOCS_LOOKUP),
                item_completed(LIST_FILES),
                final_text("Done."),
                turn_completed(),
            ],
            tools=recorder.observing(ToolSchemaFakeTools()),
            emit=emit,
        )

        assert recorder.events == [
            shown_tool("exec"),
            shown_tool("mcp:docs/search"),
            shown_tool("exec"),
            SHOWN_REASONING,
        ]
