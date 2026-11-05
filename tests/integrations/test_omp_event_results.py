"""Native event results validate against their event's payload contract."""

from __future__ import annotations

import pytest
from pydantic import JsonValue, ValidationError

from band.integrations.ompwire import OmpEvent, OmpEventType


class TestOmpEventResults:
    @pytest.mark.parametrize(
        "details", [None, {"kind": "experimental-context-rollover", "version": 1}]
    )
    def test_compaction_preserves_summary_and_checkpoint_without_tool_content(
        self, details: JsonValue
    ) -> None:
        event = OmpEvent.model_validate(
            {
                "type": OmpEventType.AUTO_COMPACTION_END,
                "action": "context-full",
                "result": {
                    "summary": "Preserve the current task and notebook.",
                    "shortSummary": "Task checkpoint",
                    "firstKeptEntryId": "kept-entry",
                    "tokensBefore": 120000,
                    "details": details,
                },
                "aborted": False,
                "willRetry": False,
            }
        )

        assert event.result is not None
        assert event.result.model_dump() == {
            "summary": "Preserve the current task and notebook.",
            "shortSummary": "Task checkpoint",
            "firstKeptEntryId": "kept-entry",
            "tokensBefore": 120000,
            "details": details,
        }

    @pytest.mark.parametrize(
        "result",
        [
            {"summary": "A tool result is not a compaction result."},
            {
                "summary": "A tool result is not a compaction result.",
                "firstKeptEntryId": "kept-entry",
                "tokensBefore": 120000,
            },
            {"content": [{"type": "text"}]},
            {"content": "not content blocks"},
        ],
    )
    def test_tool_events_still_reject_missing_or_malformed_content(
        self, result: dict[str, JsonValue]
    ) -> None:
        with pytest.raises(ValidationError):
            OmpEvent.model_validate(
                {
                    "type": OmpEventType.TOOL_END,
                    "toolCallId": "native-call",
                    "toolName": "read",
                    "result": result,
                }
            )

    def test_compaction_rejects_a_tool_result_without_a_checkpoint(self) -> None:
        with pytest.raises(ValidationError):
            OmpEvent.model_validate(
                {
                    "type": OmpEventType.AUTO_COMPACTION_END,
                    "result": {"content": [{"type": "text", "text": "tool output"}]},
                }
            )
