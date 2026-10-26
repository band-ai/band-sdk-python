"""The model's report is read from claude-code-action's execution file."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

REPORT = {
    "status": "documented",
    "items": [
        {
            "category": "Fixed",
            "label": "Reconnect",
            "sentence": "Rooms rejoin.",
            "sources": ["7"],
        }
    ],
    "breaking": None,
}
INIT = {"type": "system", "subtype": "init", "session_id": "s"}
ASSISTANT = {"type": "assistant", "message": {"content": []}}


def execution_file(tmp_path: Path, messages: list[dict[str, object]]) -> Path:
    path = tmp_path / "claude-execution-output.json"
    path.write_text(json.dumps(messages), encoding="utf-8")
    return path


class TestExtractReport:
    def test_report_is_the_result_structured_output(
        self, extractreport: ModuleType, releasedata: ModuleType, tmp_path: Path
    ) -> None:
        result = {"type": "result", "subtype": "success", "structured_output": REPORT}
        path = execution_file(tmp_path, [INIT, ASSISTANT, result])
        assert extractreport.extract_report(
            path
        ) == releasedata.ChangelogReport.model_validate(REPORT)

    @pytest.mark.parametrize(
        ("messages", "error"),
        [
            ([INIT, ASSISTANT], "no result message"),
            (
                [INIT, {"type": "result", "subtype": "error_max_turns"}],
                "no structured_output",
            ),
        ],
        ids=["no-result", "no-structured-output"],
    )
    def test_missing_report_fails(
        self,
        extractreport: ModuleType,
        tmp_path: Path,
        messages: list[dict[str, object]],
        error: str,
    ) -> None:
        with pytest.raises(ValueError, match=error):
            extractreport.extract_report(execution_file(tmp_path, messages))

    @pytest.mark.parametrize(
        "off_schema",
        [
            REPORT | {"items": []},
            REPORT | {"items": [REPORT["items"][0] | {"label": "Two\nlines"}]},
            REPORT | {"breaking": {"title": "Two\nlines", "markdown": "Gone."}},
        ],
        ids=["documented-without-items", "multi-line-label", "multi-line-title"],
    )
    def test_off_schema_report_fails(
        self,
        extractreport: ModuleType,
        tmp_path: Path,
        off_schema: dict[str, object],
    ) -> None:
        result = {
            "type": "result",
            "subtype": "success",
            "structured_output": off_schema,
        }
        with pytest.raises(ValidationError):
            extractreport.extract_report(execution_file(tmp_path, [result]))
