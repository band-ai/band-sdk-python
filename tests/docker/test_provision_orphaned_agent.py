"""An agent `band-kit provision` registers but cannot finish is reported by id.

Drives the real REST client over HTTP and fails the real `sbx secret
set-custom` subprocess boundary after the platform has minted the agent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from band.docker.provision import (
    ORPHANED_AGENT_ID_LINE,
    PLACEHOLDER_AGENT_ID,
    main,
)
from tests.docker.launcher.fakes import default_config, make_workspace, write_config

REST_URL = "https://band.test"
AGENT_ID = "agent-orphaned"
AGENT_NAME = "orphan-proof"
MINTED_KEY = "nonsecret-minted-key"
NO_SECRETS = '{"custom_secrets": []}'


def failing_secret_injection(
    argv: list[str], **_: object
) -> subprocess.CompletedProcess:
    """`sbx`: no secret yet, then a daemon that refuses the injection."""
    if argv[1:3] == ["secret", "ls"]:
        return subprocess.CompletedProcess(argv, 0, stdout=NO_SECRETS, stderr="")
    if argv[1:3] == ["secret", "set-custom"]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="daemon down")
    raise AssertionError(f"unexpected sbx invocation: {argv}")


@pytest.fixture
def placeholder_workspace(tmp_path: Path) -> Path:
    workspace = make_workspace(tmp_path)
    config = default_config(workspace)
    config["agent"]["id"] = PLACEHOLDER_AGENT_ID
    write_config(workspace, config)
    return workspace.root


class TestOrphanedAgentReported:
    def test_cli_names_the_agent_registered_before_key_injection_failed(
        self,
        placeholder_workspace: Path,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        httpx_mock.add_response(
            method="POST",
            url=f"{REST_URL}/api/v1/me/agents/register",
            json={
                "data": {
                    "agent": {
                        "id": AGENT_ID,
                        "inserted_at": "2026-01-01T00:00:00Z",
                        "name": AGENT_NAME,
                        "owner_id": "owner-1",
                    },
                    "credentials": {"api_key": MINTED_KEY},
                }
            },
        )
        monkeypatch.setattr(
            "band.docker.sbx_process.subprocess.run", failing_secret_injection
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "band-kit",
                "provision",
                "--name",
                "orphan-sandbox",
                "--agent-name",
                AGENT_NAME,
                "--description",
                "d",
                "--workspace",
                str(placeholder_workspace),
                "--api-key",
                "nonsecret-user-key",
                "--rest-url",
                REST_URL,
                "--no-create",
            ],
        )

        with pytest.raises(SystemExit) as exit_info:
            main()

        stderr = capsys.readouterr().err
        assert exit_info.value.code == 1
        assert f"{ORPHANED_AGENT_ID_LINE}{AGENT_ID}" in stderr.splitlines()
        assert MINTED_KEY not in stderr
