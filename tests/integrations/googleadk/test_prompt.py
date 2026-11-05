"""Render bounded ADK prompts without changing supplied history."""

from __future__ import annotations

from copy import deepcopy

from band.adapters.google_adk import (
    GoogleADKAdapter,
    GoogleADKAdapterConfig,
    TurnPrompt,
)
from band.runtime.formatters import format_current_inputs
from tests.framework_conformance.turnprobes import user_message


class TestPrompt:
    def test_repeated_rendering_preserves_history_with_overlap_and_limits(self) -> None:
        current = user_message("CURRENT-ASK")
        older_context = "[Alice]: Older line"
        retained_context = "LATEST-CONTEXT"
        recent_context = f"[Alice]: Recent\n{retained_context}"
        history = [
            {"id": "oldest", "role": "user", "content": "OUTSIDE-WINDOW"},
            {"id": "older", "role": "user", "content": older_context},
            {"id": current.id, "role": "user", "content": current.format_for_llm()},
            {"id": "recent", "role": "user", "content": recent_context},
        ]
        original_history = deepcopy(history)
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_history_messages=2, max_transcript_chars=20)
        )
        expected = TurnPrompt(
            text=(
                f"[Previous conversation context]\n{retained_context}\n"
                "[End of previous context]\n\n\n"
                f"{format_current_inputs([current])}"
            ),
            original_transcript_chars=len(f"{older_context}\n{recent_context}"),
            retained_transcript_chars=len(retained_context),
        )

        assert adapter._build_turn_prompt(history, [current], None, None) == expected
        assert history == original_history
        assert adapter._build_turn_prompt(history, [current], None, None) == expected
        assert history == original_history
