"""Meeting-demo cleanup must stay on the deployment that created its ids."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from band_rest.core.api_error import ApiError

from tests.loaders import load_script_module

provision = load_script_module(
    "examples/docker_demo/provision.py", "docker_demo_provision_deployment"
)

ORIGINAL = "https://original.band.test"
REPLACEMENT = "https://replacement.band.test"


@pytest.fixture
def demo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(provision, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(provision, "AGENT_IDS", tmp_path / "agent_ids.txt")
    monkeypatch.setattr(provision, "ROOM_IDS", tmp_path / "room_ids.txt")
    monkeypatch.setattr(provision, "CONFIG_PATH", tmp_path / "agent_config.yaml")
    monkeypatch.setattr(provision, "AGENTS_ENV", tmp_path / "agents.env")
    return tmp_path


class TestProvisionDeploymentBinding:
    """A 404 from another deployment is not evidence a demo agent is gone."""

    async def test_registration_records_the_supplied_deployment(
        self, demo_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        del demo_dir
        spec = provision.SPECS[0]
        monkeypatch.setattr(provision, "SPECS", [spec])
        monkeypatch.setenv("BAND_REST_URL", REPLACEMENT)
        rest = AsyncMock()
        rest.human_api_agents.register_my_agent.return_value = SimpleNamespace(
            data=SimpleNamespace(
                agent=SimpleNamespace(id="agent-1", name=spec.name),
                credentials=SimpleNamespace(api_key="test-key"),
            )
        )

        await provision.create(rest, deployment_url=f"{ORIGINAL}/")

        assert (
            provision.deployment_path().read_text(encoding="utf-8").strip() == ORIGINAL
        )

    async def test_existing_deployment_binding_blocks_registration(
        self, demo_dir: Path
    ) -> None:
        del demo_dir
        provision.deployment_path().write_text(f"{ORIGINAL}\n")
        rest = AsyncMock()

        with pytest.raises(ValueError, match="already exists"):
            await provision.create(rest)

        rest.human_api_agents.register_my_agent.assert_not_called()

    async def test_delete_on_another_deployment_keeps_recorded_ids(
        self, demo_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        del demo_dir
        provision.AGENT_IDS.write_text("agent-1\n")
        provision.deployment_path().write_text(f"{ORIGINAL}\n")
        monkeypatch.setenv("BAND_REST_URL", REPLACEMENT)
        rest = AsyncMock()
        rest.human_api_agents.delete_my_agent.side_effect = ApiError(status_code=404)

        with pytest.raises(RuntimeError, match=ORIGINAL):
            await provision.delete(rest)

        rest.human_api_agents.delete_my_agent.assert_not_awaited()
        assert provision.AGENT_IDS.read_text(encoding="utf-8").splitlines() == [
            "agent-1"
        ]
        assert provision.deployment_path().exists()

    async def test_room_delete_on_another_deployment_keeps_the_ledger(
        self, demo_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        del demo_dir
        provision.record_room("room-1")
        provision.deployment_path().write_text(f"{ORIGINAL}\n")
        monkeypatch.setenv("BAND_REST_URL", REPLACEMENT)
        client = AsyncMock()

        with pytest.raises(RuntimeError, match=ORIGINAL):
            await provision.delete_rooms(client)

        client.human_api_bulk_deletions.bulk_delete_my_chats.assert_not_called()
        assert provision.ROOM_IDS.read_text(encoding="utf-8").split() == ["room-1"]

    async def test_matching_deployment_accepts_a_missing_agent(
        self, demo_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        del demo_dir
        provision.AGENT_IDS.write_text("agent-1\n")
        provision.deployment_path().write_text(f"{ORIGINAL}/\n")
        monkeypatch.setenv("BAND_REST_URL", ORIGINAL)
        rest = AsyncMock()
        rest.human_api_agents.delete_my_agent.side_effect = ApiError(status_code=404)

        await provision.delete(rest)

        assert not provision.AGENT_IDS.exists()
        assert not provision.deployment_path().exists()
