"""Self-registration teardown attempts the agent and its scoped secret independently."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit import sandbox_agent as registration
from tests.e2e.baseline.settings import BaselineSettings


class TestSelfRegistrationCleanup:
    async def test_self_registration_room_failure_still_attempts_both_cleanup_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "band.yaml").write_text("agent:\n  id: registered-agent\n")
        monkeypatch.setattr(
            registration, "prepare_workspace", lambda *a, **k: workspace
        )
        monkeypatch.setattr(
            registration, "provision_run", AsyncMock(return_value="registered-agent")
        )
        manager = MagicMock()
        manager.provision_room = AsyncMock(
            side_effect=RuntimeError("room create blocked")
        )
        manager.reap_agent = AsyncMock(
            side_effect=RuntimeError("agent cleanup blocked")
        )
        secret_cleanup = MagicMock(side_effect=subprocess.TimeoutExpired("sbx", 60))
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)
        settings = BaselineSettings()
        settings.credentials = settings.credentials.model_copy(
            update={"api_key_user": "nonsecret-user-fixture"}
        )
        registered = registration.self_registered_sandbox(
            tmp_path, manager, settings, SimpleNamespace(tag="nonsecret-image")
        )

        with pytest.raises(ExceptionGroup) as caught:
            async with registered:
                pass

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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "band.yaml").write_text("agent:\n  id: persisted-agent\n")
        monkeypatch.setattr(
            registration, "prepare_workspace", lambda *a, **k: workspace
        )
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
        registered = registration.self_registered_sandbox(
            tmp_path, manager, settings, SimpleNamespace(tag="nonsecret-image")
        )

        with pytest.raises(RuntimeError, match="injection blocked"):
            async with registered:
                pass

        manager.reap_agent.assert_awaited_once_with("persisted-agent")
        secret_cleanup.assert_called_once()

    async def test_secret_cleanup_requires_confirmed_absence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "band.yaml").write_text("agent:\n  id: persisted-agent\n")
        monkeypatch.setattr(
            registration, "prepare_workspace", lambda *a, **k: workspace
        )
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
        registered = registration.self_registered_sandbox(
            tmp_path, manager, settings, SimpleNamespace(tag="nonsecret-image")
        )

        with pytest.raises(ExceptionGroup) as caught:
            async with registered:
                pass

        manager.reap_agent.assert_awaited_once_with("persisted-agent")
        assert "Scoped secret still present" in str(caught.value.exceptions[0])
        assert [call.args[0][1:3] for call in secret_cleanup.call_args_list] == [
            ["secret", "ls"],
            ["secret", "rm"],
            ["secret", "ls"],
        ]
