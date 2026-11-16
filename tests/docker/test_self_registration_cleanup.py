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
from tests.docker.toolkit import sandbox_agent as registration
from tests.e2e.baseline.toolkit.provisioning import (
    NAME_PREFIX,
    NAME_TOKEN_HEX,
    TOKEN_LABEL_SEPARATOR,
)

Start = Callable[[Any], AbstractAsyncContextManager[tuple[str, str, Any]]]
NO_SECRETS = SimpleNamespace(returncode=0, stdout='{"custom_secrets": []}')


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
        assert caught.value.exceptions[0].__notes__ == [
            "Cleanup incomplete for agent registered-agent"
        ]
        assert isinstance(caught.value.__context__, RuntimeError)
        assert str(caught.value.__context__) == "room create blocked"

    @pytest.mark.parametrize(
        ("registered_id", "expected_reaps"),
        [
            pytest.param(
                "registered-agent", [call("registered-agent")], id="registered-anyway"
            ),
            pytest.param(None, [], id="never-registered"),
        ],
    )
    async def test_registration_failure_reaps_only_an_agent_found_by_its_swept_name(
        self,
        start_registration: Start,
        monkeypatch: pytest.MonkeyPatch,
        registered_id: str | None,
        expected_reaps: list[Any],
    ) -> None:
        """A client-side registration timeout may or may not have registered the agent."""
        provision = AsyncMock(side_effect=RuntimeError("registration timed out"))
        monkeypatch.setattr(registration, "provision_run", provision)
        manager = MagicMock(
            find_agent_id=AsyncMock(return_value=registered_id), reap_agent=AsyncMock()
        )
        secret_cleanup = MagicMock(return_value=NO_SECRETS)
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)

        with pytest.raises(RuntimeError, match="registration timed out"):
            async with start_registration(manager):
                pass

        registered_name = provision.await_args.args[0].agent_name
        assert registered_name.startswith(NAME_PREFIX)
        token = registered_name.removeprefix(NAME_PREFIX).split(TOKEN_LABEL_SEPARATOR)[
            0
        ]
        # A full uuid token: the exact-name lookup can only find this run's agent.
        assert len(token) == NAME_TOKEN_HEX
        manager.find_agent_id.assert_awaited_once_with(registered_name)
        assert manager.reap_agent.await_args_list == expected_reaps
        secret_cleanup.assert_called_once()

    async def test_cancelled_agent_cleanup_still_removes_the_scoped_secret(
        self, start_registration: Start, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registration,
            "provision_run",
            AsyncMock(side_effect=RuntimeError("registration timed out")),
        )
        manager = MagicMock(
            find_agent_id=AsyncMock(return_value="registered-agent"),
            reap_agent=AsyncMock(side_effect=asyncio.CancelledError),
        )
        secret_cleanup = MagicMock(return_value=NO_SECRETS)
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)

        with pytest.raises(asyncio.CancelledError):
            async with start_registration(manager):
                pass

        secret_cleanup.assert_called_once()

    async def test_secret_cleanup_requires_confirmed_absence(
        self, start_registration: Start, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registration,
            "provision_run",
            AsyncMock(side_effect=RuntimeError("setup blocked")),
        )
        manager = MagicMock(
            find_agent_id=AsyncMock(return_value="persisted-agent"),
            reap_agent=AsyncMock(),
        )
        listing = SimpleNamespace(
            stdout='{ "custom_secrets": [{"placeholder": "'
            + PROXY_MANAGED_API_KEY
            + '"}] }'
        )
        secret_cleanup = MagicMock(
            side_effect=[listing, SimpleNamespace(returncode=0), listing]
        )
        monkeypatch.setattr(registration.subprocess, "run", secret_cleanup)

        with pytest.raises(ExceptionGroup) as caught:
            async with start_registration(manager):
                pass

        manager.reap_agent.assert_awaited_once_with("persisted-agent")
        assert "Scoped secret still present" in str(caught.value.exceptions[0])
        assert [call.args[0][1:3] for call in secret_cleanup.call_args_list] == [
            ["secret", "ls"],
            ["secret", "rm"],
            ["secret", "ls"],
        ]
