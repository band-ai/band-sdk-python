"""Verify the native Codex approval decision and its shell write effect."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.codex import CodexAdapter, CodexSandboxMode
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    appending_command,
    written_lines,
)
from tests.e2e.baseline.smoke.samples.codexpeer import (
    NATIVE_DEADLINE_S,
    ShellPeer,
    finish_shell_turn,
    native_client,
    required_approval,
    shell_peer,
    start_shell_turn,
)
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.adapters import Adapter

APPROVED_MARKER = "approved-shell-write"


class TestCodexShellApproval:
    @lane(Lane.BACKENDS)
    @requires(Dep.CODEX_CLI)
    @pytest.mark.parametrize(
        "decision,status,exit_code,expected_lines",
        [
            pytest.param("accept", "completed", 0, [APPROVED_MARKER], id="approve"),
            pytest.param("decline", "declined", None, None, id="decline"),
        ],
    )
    @pytest.mark.asyncio(loop_scope="session")
    async def test_codex_shell_approval_is_required(
        self,
        baseline_settings: BaselineSettings,
        tmp_path: Path,
        decision: str,
        status: str,
        exit_code: int | None,
        expected_lines: list[str] | None,
    ) -> None:
        """A non-escalated write must ask before its effect, even when declined."""
        workdir = tmp_path.resolve()
        done = unique_marker("closed")
        target = workdir / "approval.txt"
        adapter = DIALECTS[Adapter.CODEX].build(
            baseline_settings,
            AgentSetup(workdir, NATIVE_DEADLINE_S),
        )
        assert isinstance(adapter, CodexAdapter)
        # Keep untrusted command approval while allowing the approved append inside
        # the disposable workspace without relying on a sandbox denial and retry.
        config = adapter.config.model_copy(
            update={"sandbox": CodexSandboxMode.WORKSPACE_WRITE}
        )
        peer = ShellPeer(appending_command(APPROVED_MARKER, target), workdir, done)
        async with (
            shell_peer(peer) as url,
            native_client(config, workdir / "home", workdir, url) as client,
        ):
            async with asyncio.timeout(NATIVE_DEADLINE_S):
                await start_shell_turn(client, config, workdir)
                approval = await required_approval(client)
                assert not target.exists(), "Shell write occurred before approval"
                assert approval.id is not None
                assert isinstance(approval.params, dict)
                assert Path(approval.params["cwd"]) == workdir, approval.params
                await client.respond(approval.id, {"decision": decision})
                result = await finish_shell_turn(client)
        assert peer.calls == 1
        assert result.extra_approvals == 0, (
            "One command requested more than one approval"
        )
        assert len(result.commands) == 1, (result.commands, peer.output)
        command = result.commands[0]
        assert command["id"] == approval.params["itemId"], command
        assert Path(command["cwd"]) == workdir, command
        assert result.reply == done
        assert (command["status"], command.get("exitCode")) == (status, exit_code), (
            command
        )
        actual_lines = written_lines(target) if target.exists() else None
        assert actual_lines == expected_lines, (target, command, peer.output)
