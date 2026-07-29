"""Unit guards for the Copilot-in-VS-Code harness toolkit (``tests/e2e/vscode``).

The live suite needs a signed-in VS Code window and can never run in CI, so
these tests are the only ones protecting its plumbing on every PR: the prompt
shape Copilot receives, the workspace files VS Code reads, the band-mcp
process lifecycle, the version evidence recorded with a run, and the scorecard
rows the run emits.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests.e2e.vscode import server as server_module
from tests.e2e.vscode.driver import (
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
from tests.e2e.vscode.server import BandMCPServer
from tests.e2e.vscode.workspace import (
    AUTO_REPLY_SETTING,
    MCP_ACCESS_ALL,
    MCP_ACCESS_SETTING,
    MCP_SERVER_NAME,
    scaffold_workspace,
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


def test_scaffold_workspace_writes_mcp_entry_and_auto_reply(tmp_path: Path) -> None:
    sse_url = "http://127.0.0.1:8391/sse"
    scaffold_workspace(tmp_path, sse_url)

    mcp = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    # Assert the contract fields, not the whole dict — a new optional key in
    # the scaffold must not break this guard.
    server = mcp["servers"][MCP_SERVER_NAME]
    assert server["type"] == "sse"
    assert server["url"] == sse_url

    settings = json.loads((tmp_path / ".vscode" / "settings.json").read_text())
    assert settings[AUTO_REPLY_SETTING] is True
    assert settings[MCP_ACCESS_SETTING] == MCP_ACCESS_ALL


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

    # Behavioral contract only (what the sidecar must convey), not join format:
    # the VS Code version+build survive on one line, copilot extensions are
    # kept while unrelated ones are filtered, band-mcp passes through.
    assert "1.103.0" in versions["vscode"] and "abc123" in versions["vscode"]
    assert "\n" not in versions["vscode"]
    assert "github.copilot@1.5.0" in versions["copilot_extensions"]
    assert "github.copilot-chat@0.32.0" in versions["copilot_extensions"]
    assert "ms-python" not in versions["copilot_extensions"]
    assert "band-mcp 1.3.2" in versions["band_mcp"]
    assert versions["os"]


def test_capture_versions_records_failures_instead_of_raising() -> None:
    def failing(cmd: list[str]) -> str:
        raise OSError("boom")

    versions = capture_versions(["code"], ["band-mcp"], run=failing)
    assert "unavailable" in versions["vscode"]
    assert "unavailable" in versions["band_mcp"]


# --- band-mcp server lifecycle: startup failure modes --------------------------------


def _server(command: list[str], port: int = 0) -> BandMCPServer:
    return BandMCPServer(command, agent_key="key", base_url="http://band", port=port)


async def test_start_rejects_port_held_by_another_process() -> None:
    """A stale listener must fail the run loud, not answer the readiness probe
    for a child that could never bind (the suite would then talk to the stale
    server's Band identity)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        server = _server([sys.executable], port=sock.getsockname()[1])
        with pytest.raises(RuntimeError, match="already in use"):
            await server.start()
    assert not server.running  # no child was ever spawned


async def test_failed_startup_never_orphans_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that stays alive but never binds must be terminated when start()
    gives up — the session fixture's teardown never runs on a startup failure,
    so an orphan would squat the stable port across reruns."""
    monkeypatch.setattr(server_module, "READY_TIMEOUT_S", 0.5)
    never_binds = [sys.executable, "-c", "import time; time.sleep(60)"]
    server = _server(never_binds)

    with pytest.raises(RuntimeError, match="not accepting connections"):
        await server.start()

    assert not server.running


# --- scorecard: outcome mapping + emitted artifact ----------------------------------


def _report(when: str, outcome: str, nodeid: str) -> pytest.TestReport:
    """A minimal stand-in carrying the only report fields the plugin reads."""
    return cast(
        pytest.TestReport,
        SimpleNamespace(
            when=when,
            skipped=outcome == "skipped",
            failed=outcome == "failed",
            passed=outcome == "passed",
            nodeid=nodeid,
        ),
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
    report = _report(when, outcome, "tests/e2e/vscode/test_x.py::t")
    assert outcome_status(report) == expected


def test_scorecard_keeps_suite_rows_plus_fixed_na_row(tmp_path: Path) -> None:
    """One row per suite test plus the fixed L4 N/A; foreign reports ignored.

    The filter is a nodeid prefix — nodeids stay rootdir-relative wherever
    pytest is invoked from, unlike report file paths.
    """
    nodeid = "tests/e2e/vscode/test_copilot_chat.py::test_participation"

    plugin = VSCodeScorecard(tmp_path / "scorecard.json", "tests/e2e/vscode/")
    for report in (
        _report("setup", "passed", nodeid),
        _report("call", "passed", nodeid),
        _report("call", "passed", "tests/other.py::t"),
    ):
        plugin.pytest_runtest_logreport(report)

    rows = {row.test: row for row in plugin.scorecard()}
    assert rows[nodeid].status == "pass"
    assert rows[nodeid].adapter == SURFACE_ID
    assert rows[USAGE_NA_ROW.test] == USAGE_NA_ROW
    assert len(rows) == 2  # the foreign report contributed nothing


def test_sessionfinish_writes_scorecard_and_metadata_sidecar(tmp_path: Path) -> None:
    out = tmp_path / "scorecard.json"
    plugin = VSCodeScorecard(out, "tests/e2e/vscode/")
    plugin.metadata = {"vscode": "1.103.0"}

    plugin.pytest_sessionfinish()

    assert json.loads(out.read_text())  # the rows artifact (N/A row at minimum)
    meta = json.loads((tmp_path / "scorecard.json.meta.json").read_text())
    assert meta == {"vscode": "1.103.0"}
