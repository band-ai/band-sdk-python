"""An owned Codex session closes real native work after provider-turn completion."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.adapters.codex import CodexAdapterConfig, CodexSandboxMode
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.codexpeer import (
    NATIVE_DEADLINE_S,
    ShellPeer,
    closing_reply,
    native_client,
    shell_peer,
    start_shell_turn,
)
from tests.e2e.baseline.smoke.samples.nativecommand import shell_command
from tests.e2e.baseline.toolkit.builders import codex_config_kwargs

if TYPE_CHECKING:
    from tests.integrations.omprpc.gate import ProcessGate


class TestCodexBackground:
    @lane(Lane.BACKENDS)
    @requires(Dep.CODEX_CLI)
    @pytest.mark.parametrize(
        "experimental_api", [True, False], ids=["native-control", "opt-out"]
    )
    @pytest.mark.asyncio(loop_scope="session")
    async def test_session_close_terminates_a_native_child_after_its_turn(
        self,
        baseline_settings: BaselineSettings,
        tmp_path: Path,
        native_process_gate: ProcessGate,
        experimental_api: bool,
    ) -> None:
        from band.integrations.omprpc.custody import process_is_alive  # noqa: PLC0415 -- psutil belongs to the backend extra
        from tests.integrations.ompgate import GATE_COMMAND, GateRole  # noqa: PLC0415

        config = CodexAdapterConfig.model_validate(
            {
                **codex_config_kwargs(
                    baseline_settings, prompt=None, workspace_root=str(tmp_path)
                ),
                "approval_policy": "never",
                "sandbox": CodexSandboxMode.DANGER_FULL_ACCESS,
                "experimental_api": experimental_api,
            }
        )
        command = shell_command(
            (
                *GATE_COMMAND,
                "--scenario",
                GateRole.CHILD,
                "--gate-port",
                str(native_process_gate.port),
            )
        )
        closing = "native child remains active"
        peer = ShellPeer(command, tmp_path, closing)
        async with shell_peer(peer) as url:
            async with native_client(
                config, tmp_path / "home", tmp_path, url
            ) as client:
                async with asyncio.timeout(NATIVE_DEADLINE_S):
                    await start_shell_turn(client, config, tmp_path)
                    child = await native_process_gate.connection()
                    reply, _ = await closing_reply(client)
                    assert reply == closing
                    assert await asyncio.to_thread(process_is_alive, child.process)
            assert not await asyncio.to_thread(process_is_alive, child.process)
