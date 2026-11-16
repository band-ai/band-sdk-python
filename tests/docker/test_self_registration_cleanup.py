"""Self-registration teardown attempts the agent and its scoped secret independently."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from band.credentials import PROXY_MANAGED_API_KEY
from band.docker.provision import OrphanedAgentError
from tests.docker.toolkit import sandbox_agent as registration
from tests.e2e.baseline.toolkit.provisioning import NAME_PREFIX

Start = Callable[[Any], AbstractAsyncContextManager[tuple[str, str, Any]]]
NO_SECRETS = SimpleNamespace(returncode=0, stdout='{"custom_secrets": []}')
SECRET_PRESENT = SimpleNamespace(
    returncode=0,
    stdout='{ "custom_secrets": [{"placeholder": "' + PROXY_MANAGED_API_KEY + '"}] }',
)
REMOVED = SimpleNamespace(returncode=0)


def secret_commands(secret_cleanup: MagicMock) -> list[list[str]]:
    """The ``sbx secret`` subcommands cleanup ran, in order."""
    return [run.args[0][1:3] for run in secret_cleanup.call_args_list]


class TestSelfRegistrationCleanup:
    async def test_self_registration_room_failure_still_attempts_both_cleanup_paths(
        self,
        registration_workspace: Path,
        start_registration: Start,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (registration_workspace / "band.yaml").write_text(
            "agent:\n  id: registered-agent\n"
        )
        monkeypatch.setattr(
            registration, "provision_run", AsyncMock(return_value="registered-agent")
        )
        manager = MagicMock(
            provision_room=AsyncMock(side_effect=RuntimeError("room create blocked")),
            reap_agent=AsyncMock(side_effect=RuntimeError("agent cleanup blocked")),
        )
        secret_cleanup = MagicMock(side_effect=subprocess.TimeoutExpired("sbx", 60))
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)

        with pytest.raises(ExceptionGroup) as caught:
            async with start_registration(manager):
                pass

        manager.reap_agent.assert_awaited_once_with("registered-agent")
        secret_cleanup.assert_called_once()
        assert [type(error) for error in caught.value.exceptions] == [
            RuntimeError,
            subprocess.TimeoutExpired,
        ]
        assert "registered-agent" in caught.value.exceptions[0].__notes__[0]
        assert isinstance(caught.value.__context__, RuntimeError)
        assert str(caught.value.__context__) == "room create blocked"

    @pytest.mark.parametrize(
        ("failure", "expected_reaps"),
        [
            pytest.param(
                OrphanedAgentError("key not persisted", agent_id="orphaned-agent"),
                [call("orphaned-agent")],
                id="orphaned",
            ),
            pytest.param(
                RuntimeError("registration refused"), [], id="nothing-registered"
            ),
        ],
    )
    async def test_registration_failure_reaps_only_a_reported_orphan(
        self,
        start_registration: Start,
        monkeypatch: pytest.MonkeyPatch,
        failure: RuntimeError,
        expected_reaps: list[Any],
    ) -> None:
        monkeypatch.setattr(
            registration, "provision_run", AsyncMock(side_effect=failure)
        )
        manager = MagicMock(reap_agent=AsyncMock())
        secret_cleanup = MagicMock(return_value=NO_SECRETS)
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)

        with pytest.raises(type(failure)):
            async with start_registration(manager):
                pass

        assert manager.reap_agent.await_args_list == expected_reaps
        secret_cleanup.assert_called_once()

    async def test_registered_agent_is_named_for_the_orphan_sweep(
        self, start_registration: Start, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent whose orphan id never surfaced is still reclaimed by the sweep."""
        provision = AsyncMock(side_effect=RuntimeError("registration outcome unknown"))
        monkeypatch.setattr(registration, "provision_run", provision)
        monkeypatch.setattr(
            registration.subprocess, "run", MagicMock(return_value=NO_SECRETS)
        )

        with pytest.raises(RuntimeError):
            async with start_registration(MagicMock(reap_agent=AsyncMock())):
                pass

        assert provision.await_args.args[0].agent_name.startswith(NAME_PREFIX)

    async def test_cancelled_agent_cleanup_still_removes_the_scoped_secret(
        self,
        registration_workspace: Path,
        start_registration: Start,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (registration_workspace / "band.yaml").write_text(
            "agent:\n  id: registered-agent\n"
        )
        monkeypatch.setattr(
            registration, "provision_run", AsyncMock(return_value="registered-agent")
        )
        manager = MagicMock(
            provision_room=AsyncMock(side_effect=RuntimeError("room create blocked")),
            reap_agent=AsyncMock(side_effect=asyncio.CancelledError),
        )
        secret_cleanup = MagicMock(side_effect=[SECRET_PRESENT, REMOVED, NO_SECRETS])
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)

        with pytest.raises(asyncio.CancelledError):
            async with start_registration(manager):
                pass

        assert secret_commands(secret_cleanup) == [
            ["secret", "ls"],
            ["secret", "rm"],
            ["secret", "ls"],
        ]

    async def test_secret_cleanup_requires_confirmed_absence(
        self, start_registration: Start, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registration,
            "provision_run",
            AsyncMock(side_effect=RuntimeError("setup blocked")),
        )
        secret_cleanup = MagicMock(
            side_effect=[SECRET_PRESENT, REMOVED, SECRET_PRESENT]
        )
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)

        with pytest.raises(ExceptionGroup) as caught:
            async with start_registration(MagicMock(reap_agent=AsyncMock())):
                pass

        assert [type(error) for error in caught.value.exceptions] == [RuntimeError]
        assert secret_commands(secret_cleanup) == [
            ["secret", "ls"],
            ["secret", "rm"],
            ["secret", "ls"],
        ]
