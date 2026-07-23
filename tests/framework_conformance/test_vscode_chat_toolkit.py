"""Unit guards for the Copilot-in-VS-Code harness toolkit (``tests/e2e/vscode``).

The live suite needs a signed-in VS Code window and can never run in CI, so
these pure-function tests are the only ones protecting its plumbing on every
PR: the prompt shape Copilot receives, the workspace files VS Code reads, the
version evidence recorded with a run, and the scorecard rows the run emits.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.vscode.driver import (
    FRESH_SESSION_PREAMBLE,
    PreflightError,
    capture_versions,
    preflight,
    turn_prompt,
)
from tests.e2e.vscode.scorecard import (
    SURFACE_ID,
    USAGE_NA_ROW,
    VSCodeScorecard,
    outcome_status,
)
from tests.e2e.vscode.workspace import (
    AUTO_APPROVE_SETTING,
    MCP_SERVER_NAME,
    scaffold_workspace,
    workspace_marker_path,
)


# --- turn_prompt: the one shape every cell submits ----------------------------------


def test_turn_prompt_carries_room_message_and_tool_contract() -> None:
    prompt = turn_prompt(
        "room-123",
        "band-agent",
        sender_name="Alex",
        message="the marker is X9",
        instruction="Echo the marker back.",
    )
    # The surface cannot read the room, so the prompt itself must carry the
    # message, the room id every tool call needs, and the reply-tool contract.
    assert "room-123" in prompt
    assert "band-agent" in prompt
    assert "the marker is X9" in prompt
    assert "Echo the marker back." in prompt
    assert "band_send_message" in prompt
    assert MCP_SERVER_NAME in prompt
    assert prompt.count("Alex") >= 2  # named as sender and as reply mention


# --- workspace scaffolding: what VS Code reads --------------------------------------


def test_scaffold_workspace_writes_mcp_entry_and_auto_approve(tmp_path: Path) -> None:
    sse_url = "http://127.0.0.1:8391/sse"
    scaffold_workspace(tmp_path, sse_url)

    mcp = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert mcp["servers"][MCP_SERVER_NAME] == {"type": "sse", "url": sse_url}

    settings = json.loads((tmp_path / ".vscode" / "settings.json").read_text())
    assert settings[AUTO_APPROVE_SETTING] is True


def test_workspace_marker_path_stays_inside_workspace(tmp_path: Path) -> None:
    marker = workspace_marker_path(tmp_path, "notes-abc.txt")
    assert marker.parent == tmp_path


# --- driver preflight + version evidence --------------------------------------------


def test_preflight_rejects_missing_binary() -> None:
    with pytest.raises(PreflightError, match="README"):
        preflight(["definitely-not-a-real-code-binary"])


def test_capture_versions_shape_with_stub_runner() -> None:
    outputs = {
        "code --version": "1.103.0\nabc123\narm64",
        "code --list-extensions --show-versions": (
            "github.copilot@1.5.0\ngithub.copilot-chat@0.32.0\nms-python.python@2026.1"
        ),
        "band-mcp --version": "band-mcp 1.3.2",
    }

    versions = capture_versions(
        ["code"], ["band-mcp"], run=lambda cmd: outputs[" ".join(cmd)]
    )

    assert versions["vscode"] == "1.103.0 abc123 arm64"
    assert versions["copilot_extensions"] == (
        "github.copilot@1.5.0; github.copilot-chat@0.32.0"
    )
    assert versions["band_mcp"] == "band-mcp 1.3.2"
    assert versions["os"]


def test_capture_versions_records_failures_instead_of_raising() -> None:
    def failing(cmd: list[str]) -> str:
        raise OSError("boom")

    versions = capture_versions(["code"], ["band-mcp"], run=failing)
    assert "unavailable" in versions["vscode"]
    assert "unavailable" in versions["band_mcp"]


def test_fresh_session_preamble_is_prependable_text() -> None:
    # The CLI has no verified new-session switch; the preamble is the fallback
    # and must stay a plain instruction line (no templating placeholders).
    assert "{" not in FRESH_SESSION_PREAMBLE


# --- scorecard: outcome mapping + emitted artifact ----------------------------------


def _report(when: str, outcome: str, nodeid: str, fspath: str) -> SimpleNamespace:
    return SimpleNamespace(
        when=when,
        skipped=outcome == "skipped",
        failed=outcome == "failed",
        passed=outcome == "passed",
        nodeid=nodeid,
        fspath=fspath,
    )


@pytest.mark.parametrize(
    ("when", "outcome", "expected"),
    [
        ("setup", "skipped", "skip"),
        ("setup", "failed", "fail"),
        ("setup", "passed", None),  # no verdict until the call phase
        ("call", "failed", "fail"),
        ("call", "passed", "pass"),
        ("teardown", "failed", None),  # teardown never overrides the call
    ],
)
def test_outcome_status_mapping(when: str, outcome: str, expected: str | None) -> None:
    report = _report(when, outcome, "tests/e2e/vscode/test_x.py::t", "x")
    assert outcome_status(report) == expected  # type: ignore[arg-type]


def test_usage_na_row_carries_surface_and_reason() -> None:
    assert USAGE_NA_ROW.adapter == SURFACE_ID
    assert USAGE_NA_ROW.status == "na"
    assert USAGE_NA_ROW.reason


def test_scorecard_writes_rows_metadata_and_fixed_na_row(tmp_path: Path) -> None:
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    test_file = suite_dir / "test_copilot_chat.py"
    test_file.touch()
    out = tmp_path / "scorecard.json"

    plugin = VSCodeScorecard(out, suite_dir)
    plugin.metadata = {"vscode": "1.103.0"}
    nodeid = "tests/e2e/vscode/test_copilot_chat.py::test_participation"
    plugin.pytest_runtest_logreport(_report("setup", "passed", nodeid, str(test_file)))  # type: ignore[arg-type]
    plugin.pytest_runtest_logreport(_report("call", "passed", nodeid, str(test_file)))  # type: ignore[arg-type]
    # A report from outside the suite dir must not add a row.
    plugin.pytest_runtest_logreport(
        _report("call", "passed", "tests/other.py::t", str(tmp_path / "other.py"))  # type: ignore[arg-type]
    )
    plugin.pytest_sessionfinish()

    rows = {row["test"]: row for row in json.loads(out.read_text())}
    assert rows[nodeid]["status"] == "pass"
    assert rows[nodeid]["adapter"] == SURFACE_ID
    assert rows[USAGE_NA_ROW.test]["status"] == "na"
    assert len(rows) == 2

    meta = json.loads((tmp_path / "scorecard.json.meta.json").read_text())
    assert meta == {"vscode": "1.103.0"}
