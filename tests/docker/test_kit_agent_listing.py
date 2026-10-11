"""Optional agent discovery must not block demo registration or sandbox cleanup."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from band_rest.core.api_error import ApiError

from tests.loaders import load_script_module

docker_demo = load_script_module(
    "examples/docker_demo/provision.py", "docker_demo_provision_listing"
)
self_registration = load_script_module(
    "examples/sandbox/self-registration/demo.py", "self_registration_demo_listing"
)


async def test_docker_demo_registers_when_agent_listing_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    spec = docker_demo.SPECS[0]
    monkeypatch.setattr(docker_demo, "SPECS", [spec])
    monkeypatch.setattr(docker_demo, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(docker_demo, "AGENT_IDS", tmp_path / "agent_ids.txt")
    monkeypatch.setattr(docker_demo, "CONFIG_PATH", tmp_path / "agent_config.yaml")
    monkeypatch.setattr(docker_demo, "AGENTS_ENV", tmp_path / "agents.env")

    client = AsyncMock()
    client.human_api_agents.list_my_agents.side_effect = ApiError(status_code=403)
    client.human_api_agents.register_my_agent.return_value = SimpleNamespace(
        data=SimpleNamespace(
            agent=SimpleNamespace(id="agent-1", name=spec.name),
            credentials=SimpleNamespace(api_key="test-key"),
        )
    )

    with caplog.at_level(logging.ERROR):
        await docker_demo.create(client)

    assert (tmp_path / "agent_ids.txt").read_text() == "agent-1\n"
    client.human_api_agents.list_my_agents.assert_not_called()


async def test_self_registration_cleans_sandbox_when_agent_listing_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = AsyncMock()
    client.human_api_agents.list_my_agents.side_effect = ApiError(status_code=403)
    teardown = Mock(return_value=True)
    monkeypatch.setattr(self_registration, "_require_settings", Mock())
    monkeypatch.setattr(
        self_registration, "user_rest_client", Mock(return_value=client)
    )
    monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
    monkeypatch.setattr(self_registration, "_teardown_sbx", teardown)

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(RuntimeError, match="Cleanup incomplete"),
    ):
        await self_registration.cleanup_by_name("band-selfreg-demo-test")

    teardown.assert_called_once_with("band-selfreg-demo-test")
    assert "Could not list agents; continuing sandbox cleanup" in caplog.text


async def test_self_registration_cleanup_attempts_every_resource_and_keeps_unresolved_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "band-selfreg-demo-recovery"
    monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
    monkeypatch.setattr(self_registration, "_require_settings", Mock())
    monkeypatch.setattr(self_registration, "user_rest_client", Mock())
    manager = SimpleNamespace(
        reap_room=AsyncMock(side_effect=RuntimeError("room blocked")),
        reap_agent=AsyncMock(),
    )
    monkeypatch.setattr(
        self_registration, "ResourceManager", Mock(return_value=manager)
    )
    monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=False))
    self_registration.record_resource(name, "rooms", "room-1")
    self_registration.record_resource(name, "agents", "agent-1")

    with pytest.raises(RuntimeError, match="Cleanup incomplete"):
        await self_registration.cleanup_by_name(name)

    manager.reap_room.assert_awaited_once_with("room-1")
    manager.reap_agent.assert_awaited_once_with("agent-1")
    assert self_registration.recorded_resources(name, "rooms") == ["room-1"]
    assert self_registration.recorded_resources(name, "agents") == []
    assert (tmp_path / name / "sandbox").exists()


def test_self_registration_ignores_inherited_endpoint_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BAND_BASE_URL", "https://stale.band.test")
    monkeypatch.setenv("BAND_API_KEY_USER", "stale-user")
    monkeypatch.setattr(self_registration, "load_dotenv", Mock())
    monkeypatch.setattr(
        self_registration,
        "dotenv_values",
        Mock(
            return_value={
                "BAND_REST_URL": "https://canonical.band.test",
                "BAND_WS_URL": "wss://canonical.band.test/socket",
                "BAND_API_KEY_USER": "canonical-user",
            }
        ),
    )
    settings = self_registration._require_settings()
    assert settings.endpoints.rest_url == "https://canonical.band.test"
    assert settings.endpoints.ws_url == "wss://canonical.band.test/socket"
    assert settings.credentials.api_key_user == "canonical-user"


@pytest.mark.parametrize(
    "kind,error",
    [
        ("agents", ApiError(status_code=404)),
        (
            "rooms",
            httpx.HTTPStatusError(
                "already gone",
                request=httpx.Request("DELETE", "https://band.test/room"),
                response=httpx.Response(404),
            ),
        ),
    ],
)
async def test_self_registration_recovery_accepts_confirmed_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    error: Exception,
) -> None:
    name = "band-selfreg-demo-absence"
    monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
    monkeypatch.setattr(self_registration, "_require_settings", Mock())
    client = AsyncMock()
    client.human_api_agents.list_my_agents.return_value = SimpleNamespace(data=[])
    monkeypatch.setattr(
        self_registration, "user_rest_client", Mock(return_value=client)
    )
    manager = SimpleNamespace(reap_room=AsyncMock(), reap_agent=AsyncMock())
    getattr(
        manager, "reap_agent" if kind == "agents" else "reap_room"
    ).side_effect = error
    monkeypatch.setattr(
        self_registration, "ResourceManager", Mock(return_value=manager)
    )
    monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=True))
    self_registration.record_resource(name, kind, "already-gone")

    await self_registration.cleanup_by_name(name)

    assert not (tmp_path / name).exists()
