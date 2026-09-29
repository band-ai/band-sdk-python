"""Optional agent discovery must not block demo registration or sandbox cleanup."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    assert "Could not list agents; continuing without stale-agent sweep" in caplog.text


async def test_self_registration_cleans_sandbox_when_agent_listing_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = AsyncMock()
    client.human_api_agents.list_my_agents.side_effect = ApiError(status_code=403)
    teardown = Mock()
    monkeypatch.setattr(self_registration, "_require_settings", Mock())
    monkeypatch.setattr(
        self_registration, "user_rest_client", Mock(return_value=client)
    )
    monkeypatch.setattr(self_registration, "ResourceManager", Mock())
    monkeypatch.setattr(self_registration, "_teardown_sbx", teardown)

    with caplog.at_level(logging.ERROR):
        await self_registration.cleanup_by_name("band-selfreg-demo-test")

    teardown.assert_called_once_with("band-selfreg-demo-test")
    assert "Could not list agents; continuing sandbox cleanup" in caplog.text
