"""Teardown of the docker demo's meeting rooms."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from band_rest import AsyncRestClient
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
