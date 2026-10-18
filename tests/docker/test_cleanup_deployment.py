"""Cleanup must delete resources on the deployment that created them."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from band_rest.core.api_error import ApiError

from tests.loaders import load_script_module

self_registration = load_script_module(
    "examples/sandbox/self-registration/demo.py",
    "self_registration_cleanup_deployment",
)

ORIGINAL = "https://original.band.test"
REPLACEMENT = "https://replacement.band.test"


def settings_for(rest_url: str) -> SimpleNamespace:
    return SimpleNamespace(endpoints=SimpleNamespace(rest_url=rest_url))


def rest_url_of(argv: list[str]) -> str:
    return argv[argv.index("--rest-url") + 1]


class TestCleanupDeploymentBinding:
    """A 404 from another deployment is not evidence the resource is gone."""

    async def test_legacy_ledger_without_binding_blocks_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = "band-selfreg-demo-legacy"
        monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
        monkeypatch.setattr(
            self_registration,
            "_require_settings",
            Mock(return_value=settings_for(REPLACEMENT)),
        )
        monkeypatch.setattr(self_registration, "user_rest_client", Mock())
        monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=True))
        manager = SimpleNamespace(reap_room=AsyncMock(), reap_agent=AsyncMock())
        monkeypatch.setattr(
            self_registration, "ResourceManager", Mock(return_value=manager)
        )
        self_registration.record_resource(name, "agents", "agent-1")

        with pytest.raises(RuntimeError, match="no recorded deployment binding"):
            await self_registration.cleanup_by_name(name)

        manager.reap_agent.assert_not_awaited()
        manager.reap_room.assert_not_awaited()
        assert self_registration.recorded_resources(name, "agents") == ["agent-1"]
        assert (tmp_path / name).exists()

    async def test_foreign_deployment_keeps_recorded_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = "band-selfreg-demo-foreign"
        monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
        monkeypatch.setattr(
            self_registration,
            "_require_settings",
            Mock(return_value=settings_for(REPLACEMENT)),
        )
        monkeypatch.setattr(self_registration, "user_rest_client", Mock())
        monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=True))
        manager = SimpleNamespace(reap_room=AsyncMock(), reap_agent=AsyncMock())
        manager.reap_agent.side_effect = ApiError(status_code=404)
        monkeypatch.setattr(
            self_registration, "ResourceManager", Mock(return_value=manager)
        )
        self_registration.record_resource(name, "rooms", "room-1")
        self_registration.record_resource(name, "agents", "agent-1")
        self_registration.record_deployment(name, ORIGINAL)

        with pytest.raises(RuntimeError, match=ORIGINAL):
            await self_registration.cleanup_by_name(name)

        manager.reap_agent.assert_not_awaited()
        manager.reap_room.assert_not_awaited()
        assert self_registration.recorded_resources(name, "agents") == ["agent-1"]
        assert self_registration.recorded_resources(name, "rooms") == ["room-1"]

    async def test_matching_deployment_accepts_confirmed_absence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = "band-selfreg-demo-absence"
        monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
        monkeypatch.setattr(self_registration, "_require_settings", Mock())
        monkeypatch.setattr(self_registration, "user_rest_client", Mock())
        monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=True))
        manager = SimpleNamespace(
            reap_room=AsyncMock(),
            reap_agent=AsyncMock(side_effect=ApiError(status_code=404)),
        )
        monkeypatch.setattr(
            self_registration, "ResourceManager", Mock(return_value=manager)
        )
        self_registration.record_resource(name, "agents", "already-gone")
        self_registration.record_deployment(name, f"{ORIGINAL}/")

        await self_registration.cleanup_by_name(name, settings=settings_for(ORIGINAL))

        manager.reap_agent.assert_awaited_once_with("already-gone")
        assert not (tmp_path / name).exists()

    async def test_passed_settings_are_not_reloaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = "band-selfreg-demo-kept"
        loads: list[str] = []

        def require_settings() -> SimpleNamespace:
            loads.append(REPLACEMENT)
            return settings_for(REPLACEMENT)

        monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
        monkeypatch.setattr(self_registration, "_require_settings", require_settings)
        monkeypatch.setattr(self_registration, "user_rest_client", Mock())
        monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=True))
        manager = SimpleNamespace(
            reap_room=AsyncMock(),
            reap_agent=AsyncMock(side_effect=ApiError(status_code=404)),
        )
        monkeypatch.setattr(
            self_registration, "ResourceManager", Mock(return_value=manager)
        )
        self_registration.record_resource(name, "agents", "agent-1")
        self_registration.record_deployment(name, ORIGINAL)

        await self_registration.cleanup_by_name(name, settings=settings_for(ORIGINAL))

        assert loads == []
        assert not (tmp_path / name).exists()

    async def test_automatic_teardown_keeps_the_originating_deployment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loads: list[str] = []
        managers: list[str] = []
        clients: list[str] = []
        commands: list[list[str]] = []

        def require_settings() -> SimpleNamespace:
            rest_url = ORIGINAL if not loads else REPLACEMENT
            loads.append(rest_url)
            return settings_for(rest_url)

        def rest_client(settings: SimpleNamespace) -> SimpleNamespace:
            clients.append(settings.endpoints.rest_url)
            return SimpleNamespace()

        def build_manager(
            *, user_client: object, settings: SimpleNamespace, run_id: str
        ) -> SimpleNamespace:
            del user_client, run_id
            managers.append(settings.endpoints.rest_url)
            return SimpleNamespace(
                user_ops=SimpleNamespace(add_participant=AsyncMock()),
                provision_room=AsyncMock(return_value="room-1"),
                reap_room=AsyncMock(),
                reap_agent=AsyncMock(),
            )

        def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
            commands.append(list(argv))
            return SimpleNamespace(returncode=0, stdout="agent-1\n", stderr="")

        monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
        monkeypatch.setattr(self_registration, "_require_settings", require_settings)
        monkeypatch.setattr(self_registration, "user_rest_client", rest_client)
        monkeypatch.setattr(self_registration, "ResourceManager", build_manager)
        monkeypatch.setattr(self_registration.subprocess, "run", fake_run)
        monkeypatch.setattr(
            self_registration, "sandbox_name", lambda prefix: "band-selfreg-demo-bound"
        )
        monkeypatch.setattr(
            self_registration,
            "_prepare_workspace",
            lambda directory, *, endpoints: tmp_path,
        )
        monkeypatch.setattr(
            self_registration, "_deployment_hosts", lambda endpoints: []
        )
        monkeypatch.setattr(
            self_registration,
            "allow_network_for_hosts",
            lambda hosts, *, kit: nullcontext(),
        )
        monkeypatch.setattr(
            self_registration, "attached_run", lambda name: nullcontext()
        )
        monkeypatch.setattr(
            self_registration, "read_agent_id", lambda workspace: "agent-1"
        )
        monkeypatch.setattr(
            self_registration,
            "_ping_and_await_echo",
            AsyncMock(return_value=SimpleNamespace(assert_contains_any=Mock())),
        )
        monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=True))

        await self_registration.run("band-python-kit:published")

        assert loads == [ORIGINAL]
        assert managers == [ORIGINAL, ORIGINAL]
        assert clients == [ORIGINAL, ORIGINAL]
        assert [rest_url_of(argv) for argv in commands] == [ORIGINAL, ORIGINAL]
