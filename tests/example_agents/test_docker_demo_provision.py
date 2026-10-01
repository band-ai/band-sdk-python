"""Teardown of the docker demo's meeting rooms."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from tests.loaders import load_script_module

provision = load_script_module(
    "examples/docker_demo/provision.py", "docker_demo_provision"
)

REST_URL = "https://band.test"
SETTINGS = provision.ProvisionSettings(band_api_key_user="key", band_rest_url=REST_URL)


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the demo's state directory at a scratch dir."""
    monkeypatch.setattr(provision, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(provision, "ROOM_IDS", tmp_path / "room_ids.txt")
    return provision.ROOM_IDS


def room_url(room_id: str) -> str:
    return f"{REST_URL}/api/v1/me/chats/{room_id}"


async def test_recorded_rooms_are_deleted_and_the_ledger_cleared(
    ledger: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(method="DELETE", url=room_url("room-1"), status_code=204)
    httpx_mock.add_response(method="DELETE", url=room_url("room-2"), status_code=404)
    provision.record_room("room-1")
    provision.record_room("room-2")

    await provision.delete_rooms(SETTINGS)

    assert [r.headers["X-API-Key"] for r in httpx_mock.get_requests()] == [
        "key",
        "key",
    ]
    assert not ledger.exists()


async def test_a_failed_delete_keeps_the_ledger_for_a_retry(
    ledger: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(method="DELETE", url=room_url("room-1"), status_code=500)
    provision.record_room("room-1")

    with pytest.raises(Exception, match="500"):
        await provision.delete_rooms(SETTINGS)

    assert ledger.read_text(encoding="utf-8").split() == ["room-1"]
