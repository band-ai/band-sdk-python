from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.types import Emit
from band.runtime.activity import tool_call_activity
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.adapters.activityrecorder import EndNotifyingRecorder
from tests.adapters.codex.activity.kit import (
    BAND_TOOL_CALL,
    DOCS_LOOKUP,
    LIST_FILES,
    item_completed,
    item_started,
)
from tests.adapters.codex.kit import ToolSchemaFakeTools, run_codex_turn, send_bootstrap
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    turn_completed,
)

NARRATION = pytest.mark.parametrize(
    "emit", [(Emit.TOOL_CALLS,), ()], ids=["narration-on", "narration-off"]
)
SECOND_COMMAND = {**LIST_FILES, "id": "cmd-2"}


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
    @pytest.mark.parametrize(
        ("second", "first_finished", "remaining", "remaining_name", "expected"),
        [
            (
                DOCS_LOOKUP,
                LIST_FILES,
                DOCS_LOOKUP,
                "mcp:docs/search",
                [shown_tool("exec"), shown_tool("mcp:docs/search"), SHOWN_REASONING],
            ),
            (
                DOCS_LOOKUP,
                DOCS_LOOKUP,
                LIST_FILES,
                "exec",
                [
                    shown_tool("exec"),
                    shown_tool("mcp:docs/search"),
                    shown_tool("exec"),
                    SHOWN_REASONING,
                ],
            ),
            (
                SECOND_COMMAND,
                LIST_FILES,
                SECOND_COMMAND,
                "exec",
                [shown_tool("exec"), SHOWN_REASONING],
            ),
            (
                SECOND_COMMAND,
                SECOND_COMMAND,
                LIST_FILES,
                "exec",
                [shown_tool("exec"), SHOWN_REASONING],
            ),
        ],
        ids=[
            "different-older-first",
            "different-newer-first",
            "same-older-first",
            "same-newer-first",
        ],
    )
    async def test_finishing_one_item_keeps_the_other_visible(
        self,
        emit: tuple[Emit, ...],
        second: dict[str, Any],
        first_finished: dict[str, Any],
        remaining: dict[str, Any],
        remaining_name: str,
        expected: list[dict[str, Any]],
    ) -> None:
        recorder = EndNotifyingRecorder(first_finished["id"])
        client = FakeCodexClient(
            events=[
                item_started(LIST_FILES),
                item_started(second),
                item_completed(first_finished),
            ]
        )
        adapter = make_codex_adapter(client, emit=emit)
        await adapter.on_started("Codex Agent", "A coding agent")
        sending = asyncio.create_task(
            send_bootstrap(adapter, tools=recorder.observing(ToolSchemaFakeTools()))
        )
        try:
            await recorder.ended.wait()
            assert recorder.activity.detail == tool_call_activity(remaining_name)
        finally:
            recorder.release.set()
            client.emit_turn(
                "turn-1",
                item_completed(remaining),
                final_text("Done."),
                turn_completed(),
            )
            await sending
            await adapter.cleanup_all()

        assert recorder.events == expected
