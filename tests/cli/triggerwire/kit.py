"""Kit for band-trigger on the wire: the platform's response bodies, the
routes each auth mode reaches, and a readable projection of sent requests."""

from __future__ import annotations

import argparse
import json
from enum import StrEnum
from typing import Any, NamedTuple

import httpx
from pytest_httpx import HTTPXMock

REST_URL = "https://band.test/"
API_KEY = "trigger-key"
MESSAGE = "Run the daily report"
ROOM_ID = "6f1c2a4e-0b7d-4c55-9d3e-1a2b3c4d5e6f"
MESSAGE_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
TIMESTAMP = "2026-01-01T00:00:00Z"
PEERS_PAGE_SIZE = 100

TARGET = {
    "id": "2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f",
    "name": "Reporter",
    "handle": "owner/reporter",
    "type": "Agent",
}
BYSTANDER = {
    "id": "7e6d5c4b-3a29-4180-9f8e-7d6c5b4a3928",
    "name": "Bystander",
    "handle": "owner/bystander",
    "type": "Agent",
}


class AuthMode(StrEnum):
    """band-trigger's ``--auth-mode`` choices."""

    AGENT = "agent"
    USER = "user"


API_PREFIX: dict[AuthMode, str] = {
    AuthMode.AGENT: "/api/v1/agent",
    AuthMode.USER: "/api/v1/me",
}


class WireCall(NamedTuple):
    """One request as the platform received it."""

    method: str
    path: str
    params: dict[str, str]
    body: Any


def wire_calls(requests: list[httpx.Request]) -> list[WireCall]:
    """Project sent requests onto what the platform acts on."""
    return [
        WireCall(
            request.method,
            request.url.path,
            dict(request.url.params),
            json.loads(request.content) if request.content else None,
        )
        for request in requests
    ]


def trigger_args(mode: AuthMode, target_handle: str) -> argparse.Namespace:
    """The parsed CLI arguments for one trigger against the mocked platform."""
    return argparse.Namespace(
        api_key=API_KEY,
        rest_url=REST_URL,
        auth_mode=mode.value,
        target_handle=target_handle,
        message=MESSAGE,
        timeout=120,
        verbose=False,
    )


def page_params(page: int) -> dict[str, str]:
    return {"page": str(page), "page_size": str(PEERS_PAGE_SIZE)}


def answer(
    httpx_mock: HTTPXMock,
    mode: AuthMode,
    method: str,
    route: str,
    body: dict[str, Any],
    *,
    status: int = 200,
    params: dict[str, str] | None = None,
) -> None:
    """Register the platform's one response to ``method`` on ``route``."""
    url = httpx.URL(
        f"{REST_URL.rstrip('/')}{API_PREFIX[mode]}{route}", params=params or {}
    )
    httpx_mock.add_response(method=method, url=url, status_code=status, json=body)


def peers_page(
    peers: list[dict[str, str]], page: int, total_pages: int
) -> dict[str, Any]:
    return {
        "data": [
            {**peer, "is_contact": False, "online": True, "source": "registry"}
            for peer in peers
        ],
        "metadata": {
            "page": page,
            "page_size": PEERS_PAGE_SIZE,
            "total_count": len(peers),
            "total_pages": total_pages,
        },
    }


def created_room() -> dict[str, Any]:
    return {"data": {"id": ROOM_ID, "inserted_at": TIMESTAMP, "updated_at": TIMESTAMP}}


def added_participant(peer: dict[str, str]) -> dict[str, Any]:
    return {"data": {**peer, "role": "member", "status": "active"}}


def message_sent(recipient: dict[str, str]) -> dict[str, Any]:
    return {
        "data": {
            "id": MESSAGE_ID,
            "recipients": [
                {
                    "id": recipient["id"],
                    "handle": recipient["handle"],
                    "name": recipient["name"],
                }
            ],
            "success": True,
        }
    }


def platform_error(code: str, message: str) -> dict[str, Any]:
    """The platform's ``Error`` envelope."""
    return {"error": {"code": code, "message": message, "request_id": "req-1"}}


def validation_error(message: str, details: dict[str, list[str]]) -> dict[str, Any]:
    """The platform's 422 ``ValidationError`` envelope."""
    return {
        "error": {
            "code": "validation_error",
            "message": message,
            "details": details,
            "request_id": "req-1",
        }
    }
