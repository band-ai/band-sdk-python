from __future__ import annotations

from band.adapters.codex import CodexAdapterConfig
from band.testing.activity import SHOWN_MEMORY, SHOWN_REASONING, ActivityRecorder
from tests.adapters.codex.activity.kit import COMPACTION, item_completed, item_started
from tests.adapters.codex.kit import ToolSchemaFakeTools, run_codex_turn
from tests.adapters.codexturns import event_notification, final_text, turn_completed


class TestCodexCompactionActivity:
    async def test_compaction_shows_memory_until_its_item_completes(
        self, recorder: ActivityRecorder
    ) -> None:
        """The deprecated context/compacted notice that follows only posts its
        task event."""
        await run_codex_turn(
            events=[
                item_started(COMPACTION),
                item_completed(COMPACTION),
                event_notification(
                    "context/compacted", {"threadId": "thr-1", "turnId": "turn-1"}
                ),
                final_text("Done."),
                turn_completed(),
            ],
            tools=recorder.observing(ToolSchemaFakeTools()),
            config=CodexAdapterConfig(emit_turn_lifecycle_events=True),
        )

        assert recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]
