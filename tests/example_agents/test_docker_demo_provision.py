"""Teardown of the docker demo's meeting rooms."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args
from unittest.mock import AsyncMock

import pytest
from band_rest import AsyncRestClient
from band_rest.types import BulkDeletionItemStatus, BulkDeletionJobStatus
from pytest_httpx import HTTPXMock

from tests.loaders import load_script_module

provision = load_script_module(
    "examples/docker_demo/provision.py", "docker_demo_provision"
)

REST_URL = "https://band.test"
BULK_DELETE_URL = f"{REST_URL}/api/v1/me/chats/bulk-delete"
JOB_URL = f"{REST_URL}/api/v1/me/bulk-deletions/job-1"


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the demo's state directory at a scratch dir."""
    monkeypatch.setattr(provision, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(provision, "ROOM_IDS", tmp_path / "room_ids.txt")
    monkeypatch.setattr(provision, "ROOM_DELETION_POLL_S", 0)
    return provision.ROOM_IDS


def job(status: str, results: dict[str, str]) -> dict[str, Any]:
    """A bulk-deletion job response with one result per room id."""
    return {
        "data": {
            "id": "job-1",
            "status": status,
            "force": False,
            "resource_type": "chat_room",
            "inserted_at": "2026-01-01T00:00:00Z",
            "total": len(results),
            "processed": len(results),
            "succeeded": list(results.values()).count("succeeded"),
            "failed": list(results.values()).count("failed"),
            "results": [{"id": rid, "status": st} for rid, st in results.items()],
        }
    }


def client() -> AsyncRestClient:
    return AsyncRestClient(api_key="key", base_url=REST_URL)


async def test_recorded_rooms_are_deleted_and_the_ledger_cleared(
    ledger: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=BULK_DELETE_URL,
        match_json={"ids": ["room-1", "room-2"]},
        json=job("queued", {"room-1": "pending", "room-2": "pending"}),
    )
    httpx_mock.add_response(
        url=JOB_URL,
        json=job("completed", {"room-1": "succeeded", "room-2": "succeeded"}),
    )
    provision.record_room("room-1")
    provision.record_room("room-2")

    await provision.delete_rooms(client())

    assert not ledger.exists()


async def test_a_room_left_undeleted_keeps_the_ledger_for_a_retry(
    ledger: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=BULK_DELETE_URL,
        json=job("failed", {"room-1": "failed"}),
    )
    provision.record_room("room-1")

    with pytest.raises(RuntimeError, match="room-1"):
        await provision.delete_rooms(client())

    assert ledger.read_text(encoding="utf-8").split() == ["room-1"]


def literal_values(alias: object) -> set[object]:
    """The values of a generated ``Literal[...] | Any`` status alias."""
    return {value for arm in get_args(alias) for value in get_args(arm)}


def test_status_constants_are_values_the_client_declares() -> None:
    assert provision.FINISHED_JOB_STATUSES <= literal_values(BulkDeletionJobStatus)
    assert provision.DELETED_ITEM_STATUS in literal_values(BulkDeletionItemStatus)


@pytest.fixture
def provision_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(provision, "DEMO_DIR", tmp_path)
    for name, filename in (
        ("AGENT_IDS", "agent_ids.txt"),
        ("ROOM_IDS", "room_ids.txt"),
        ("CONFIG_PATH", "agent_config.yaml"),
        ("AGENTS_ENV", "agents.env"),
    ):
        monkeypatch.setattr(provision, name, tmp_path / filename)
    return tmp_path


async def test_existing_credential_directory_blocks_registration(
    provision_files: Path,
) -> None:
    provision.CONFIG_PATH.mkdir()
    rest = AsyncMock()
    with pytest.raises(ValueError, match="already exists"):
        await provision.create(rest)
    rest.human_api_agents.register_my_agent.assert_not_called()


async def test_dangling_credential_symlink_blocks_registration(
    provision_files: Path,
) -> None:
    target = provision_files / "unowned"
    try:
        provision.CONFIG_PATH.symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit creating symlinks")
    rest = AsyncMock()
    with pytest.raises(ValueError, match="already exists"):
        await provision.create(rest)
    rest.human_api_agents.register_my_agent.assert_not_called()
    assert not target.exists()


@pytest.fixture
def registered_agents() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            data=SimpleNamespace(
                agent=SimpleNamespace(id=f"agent-{index}", name=spec.name),
                credentials=SimpleNamespace(api_key=f"fixture-key-{index}"),
            )
        )
        for index, spec in enumerate(provision.SPECS)
    ]


async def test_generated_credentials_are_private_under_permissive_umask(
    provision_files: Path, registered_agents: list[SimpleNamespace]
) -> None:
    rest = AsyncMock()
    rest.human_api_agents.register_my_agent.side_effect = registered_agents
    previous_umask = os.umask(0)
    try:
        await provision.create(rest)
    finally:
        os.umask(previous_umask)
    for path in (provision.CONFIG_PATH, provision.AGENTS_ENV):
        assert "fixture-key-0" in path.read_text()
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("artifact", ["CONFIG_PATH", "AGENTS_ENV"])
async def test_late_credential_collision_preserves_unowned_file_and_cleanup_ids(
    provision_files: Path,
    registered_agents: list[SimpleNamespace],
    artifact: str,
) -> None:
    path = getattr(provision, artifact)
    responses = iter(registered_agents)

    async def register(**kwargs: Any) -> SimpleNamespace:
        path.write_text("unowned credentials")
        return next(responses)

    rest = AsyncMock()
    rest.human_api_agents.register_my_agent.side_effect = register
    with pytest.raises(FileExistsError):
        await provision.create(rest)
    assert path.read_text() == "unowned credentials"
    assert provision.AGENT_IDS.read_text().splitlines() == [
        agent.data.agent.id for agent in registered_agents
    ]


async def test_partial_registration_keeps_created_agent_in_cleanup_ledger(
    provision_files: Path,
) -> None:
    rest = AsyncMock()
    rest.human_api_agents.register_my_agent.side_effect = [
        SimpleNamespace(
            data=SimpleNamespace(
                agent=SimpleNamespace(id="agent-1", name="Maya (PM)"),
                credentials=SimpleNamespace(api_key="fixture-key"),
            )
        ),
        RuntimeError("registration blocked"),
    ]
    with pytest.raises(RuntimeError, match="registration blocked"):
        await provision.create(rest)
    assert provision.AGENT_IDS.read_text().splitlines() == ["agent-1"]
    assert not provision.CONFIG_PATH.exists()
    rest.human_api_agents.delete_my_agent.assert_not_called()


async def test_cleanup_continues_after_failed_agent_and_retains_only_unresolved(
    provision_files: Path,
) -> None:
    provision.AGENT_IDS.write_text("agent-blocked\nagent-deleted\n")
    rest = AsyncMock()
    rest.human_api_agents.delete_my_agent.side_effect = [
        RuntimeError("cleanup blocked"),
        None,
    ]
    with pytest.raises(RuntimeError, match="Cleanup incomplete"):
        await provision.delete(rest)
    assert rest.human_api_agents.delete_my_agent.await_count == 2
    assert provision.AGENT_IDS.read_text().splitlines() == ["agent-blocked"]
