"""Acceptance teardown must attempt independent resources and fail visibly."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker import test_kit_self_registration_live as registration
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ResourceManager


@pytest.mark.parametrize("strict", [False, True])
async def test_reap_all_attempts_every_resource_and_retains_failed_ids(
    monkeypatch: pytest.MonkeyPatch, strict: bool
) -> None:
    client = AsyncMock()
    client.human_api_agents.register_my_agent.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                agent=SimpleNamespace(id=agent_id),
                credentials=SimpleNamespace(api_key="nonsecret-fixture"),
            )
        )
        for agent_id in ("blocked-agent", "removed-agent")
    ]
    resources = ResourceManager(
        user_client=client, settings=BaselineSettings(), run_id="cleanup-test"
    )
    for label in ("blocked", "removed"):
        await resources.provision_agent(label)
    for room_id in ("blocked-room", "removed-room"):
        resources.adopt_room(room_id)
    room_cleanup = AsyncMock(side_effect=[RuntimeError("room blocked"), None])
    monkeypatch.setattr(resources.user_ops, "delete_room", room_cleanup)
    client.human_api_agents.delete_my_agent.side_effect = [
        RuntimeError("agent blocked"),
        None,
    ]

    if strict:
        with pytest.raises(ExceptionGroup) as caught:
            await resources.reap_all(strict=True)
        assert [str(error) for error in caught.value.exceptions] == [
            "room blocked",
            "agent blocked",
        ]
        assert [error.__notes__ for error in caught.value.exceptions] == [
            ["Cleanup incomplete for room blocked-room"],
            ["Cleanup incomplete for agent blocked-agent"],
        ]
    else:
        await resources.reap_all()
    assert [call.args[0] for call in room_cleanup.await_args_list] == [
        "blocked-room",
        "removed-room",
    ]
    assert [
        call.args[0] for call in client.human_api_agents.delete_my_agent.await_args_list
    ] == ["blocked-agent", "removed-agent"]

    room_cleanup.reset_mock(side_effect=True)
    client.human_api_agents.delete_my_agent.reset_mock(side_effect=True)
    await resources.reap_all(strict=True)
    room_cleanup.assert_awaited_once_with("blocked-room")
    client.human_api_agents.delete_my_agent.assert_awaited_once_with(
        "blocked-agent", force=True
    )


async def test_self_registration_room_failure_still_attempts_both_cleanup_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "band.yaml").write_text("agent:\n  id: registered-agent\n")
    monkeypatch.setattr(registration, "_prepare_workspace", lambda *a, **k: workspace)
    monkeypatch.setattr(registration, "sbx_available", lambda: True)
    monkeypatch.setattr(
        registration, "provision_run", AsyncMock(return_value="registered-agent")
    )
    manager = MagicMock()
    manager.provision_room = AsyncMock(side_effect=RuntimeError("room create blocked"))
    manager.reap_agent = AsyncMock(side_effect=RuntimeError("agent cleanup blocked"))
    secret_cleanup = MagicMock(side_effect=subprocess.TimeoutExpired("sbx", 60))
    monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)
    settings = BaselineSettings()
    settings.credentials = settings.credentials.model_copy(
        update={"api_key_user": "nonsecret-user-fixture"}
    )
    fixture = registration.self_registered_sandbox.__wrapped__(
        tmp_path, manager, settings, SimpleNamespace(tag="nonsecret-image")
    )

    with pytest.raises(ExceptionGroup) as caught:
        await anext(fixture)

    manager.reap_agent.assert_awaited_once_with("registered-agent")
    secret_cleanup.assert_called_once()
    assert [type(error) for error in caught.value.exceptions] == [
        RuntimeError,
        subprocess.TimeoutExpired,
    ]
    assert caught.value.exceptions[0].__notes__ == [
        "Cleanup incomplete for agent registered-agent"
    ]
    assert isinstance(caught.value.__context__, RuntimeError)
    assert str(caught.value.__context__) == "room create blocked"


async def test_registration_failure_recovers_persisted_id_for_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "band.yaml").write_text("agent:\n  id: persisted-agent\n")
    monkeypatch.setattr(registration, "_prepare_workspace", lambda *a, **k: workspace)
    monkeypatch.setattr(registration, "sbx_available", lambda: True)
    monkeypatch.setattr(
        registration,
        "provision_run",
        AsyncMock(side_effect=RuntimeError("injection blocked")),
    )
    manager = MagicMock(reap_agent=AsyncMock())
    secret_cleanup = MagicMock(
        return_value=SimpleNamespace(returncode=0, stdout='{"custom_secrets": []}')
    )
    monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)
    settings = BaselineSettings()
    settings.credentials = settings.credentials.model_copy(
        update={"api_key_user": "nonsecret-user-fixture"}
    )
    fixture = registration.self_registered_sandbox.__wrapped__(
        tmp_path, manager, settings, SimpleNamespace(tag="nonsecret-image")
    )

    with pytest.raises(RuntimeError, match="injection blocked"):
        await anext(fixture)

    manager.reap_agent.assert_awaited_once_with("persisted-agent")
    secret_cleanup.assert_called_once()


async def test_secret_cleanup_requires_confirmed_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "band.yaml").write_text("agent:\n  id: persisted-agent\n")
    monkeypatch.setattr(registration, "_prepare_workspace", lambda *a, **k: workspace)
    monkeypatch.setattr(registration, "sbx_available", lambda: True)
    monkeypatch.setattr(
        registration,
        "provision_run",
        AsyncMock(side_effect=RuntimeError("setup blocked")),
    )
    manager = MagicMock(reap_agent=AsyncMock())
    listing = SimpleNamespace(
        stdout='{ "custom_secrets": [{"placeholder": "'
        + PROXY_MANAGED_API_KEY
        + '"}] }'
    )
    secret_cleanup = MagicMock(
        side_effect=[listing, SimpleNamespace(returncode=0), listing]
    )
    monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)
    settings = BaselineSettings()
    settings.credentials = settings.credentials.model_copy(
        update={"api_key_user": "nonsecret-user-fixture"}
    )
    fixture = registration.self_registered_sandbox.__wrapped__(
        tmp_path, manager, settings, SimpleNamespace(tag="nonsecret-image")
    )

    with pytest.raises(ExceptionGroup) as caught:
        await anext(fixture)

    manager.reap_agent.assert_awaited_once_with("persisted-agent")
    assert "Scoped secret still present" in str(caught.value.exceptions[0])
    assert [call.args[0][1:3] for call in secret_cleanup.call_args_list] == [
        ["secret", "ls"],
        ["secret", "rm"],
        ["secret", "ls"],
    ]
