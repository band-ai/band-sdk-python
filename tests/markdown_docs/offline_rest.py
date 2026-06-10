from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from band.client.rest import AsyncRestClient


def _payload_for_path(path: str, now: str) -> dict:
    if "participants" in path:
        return {
            "data": [],
            "metadata": {
                "page": 1,
                "page_size": 50,
                "total_count": 0,
                "total_pages": 0,
            },
        }
    if "/messages" in path:
        return {
            "data": {
                "id": "msg-1",
                "success": True,
                "recipients": [],
                "inserted_at": now,
                "updated_at": now,
            }
        }
    if "respond" in path:
        return {
            "data": {
                "id": "req-1",
                "status": "approved",
                "inserted_at": now,
                "updated_at": now,
            }
        }
    return {"data": {"id": "room-1", "inserted_at": now, "updated_at": now}}


def stub_offline_rest(client: AsyncRestClient) -> list[dict]:
    """Attach an offline HTTP stub to a real AsyncRestClient.

    Only the low-level httpx transport is replaced. Namespace clients and
    method signatures remain the generated Fern implementations.
    """
    captured_json: list[dict] = []

    async def fake_request(*args: object, **kwargs: object) -> object:
        path = str(args[0]) if args else ""
        body = kwargs.get("json")
        if isinstance(body, dict):
            captured_json.append(body)

        now = datetime.now(timezone.utc).isoformat()
        payload = _payload_for_path(path, now)

        class _Response:
            status_code = 200

            def json(self) -> dict:
                return payload

        return _Response()

    client._client_wrapper.httpx_client.request = AsyncMock(side_effect=fake_request)
    client._markdown_captured_json = captured_json
    return captured_json
