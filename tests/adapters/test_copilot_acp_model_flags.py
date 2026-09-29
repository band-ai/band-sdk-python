"""Typed ``model`` / ``reasoning_effort`` flags on the Copilot ACP command."""

from __future__ import annotations

import pytest

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig


def test_copilot_model_flags_are_appended_once() -> None:
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(model="claude-sonnet-5", reasoning_effort="high")
    )
    assert adapter._command[-4:] == [
        "--model",
        "claude-sonnet-5",
        "--reasoning-effort",
        "high",
    ]
    assert adapter._command.count("--model") == 1


@pytest.mark.parametrize("spliced", ["--model", "--model=gpt-6"])
def test_copilot_model_flag_in_both_places_is_refused(spliced: str) -> None:
    with pytest.raises(ValueError, match="--model is set both"):
        CopilotACPAdapter(
            CopilotACPAdapterConfig(
                command=("copilot", "--acp", spliced), model="claude-sonnet-5"
            )
        )


def test_copilot_model_flags_need_stdio() -> None:
    with pytest.raises(ValueError, match="need the stdio transport"):
        CopilotACPAdapter(
            CopilotACPAdapterConfig(host="10.0.0.5", port=8080, model="gpt-6")
        )
