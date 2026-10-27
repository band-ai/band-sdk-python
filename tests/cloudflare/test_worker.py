"""Qualify ingress and lifecycle behavior in the actual Python Worker runtime."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

ENVELOPE = {"agent_id": "agent", "room_id": "room", "event_type": "message_created"}
BODY = json.dumps(ENVELOPE)
REQUIRED = (
    "BAND_AGENT_ID",
    "BAND_REST_URL",
    "BAND_API_KEY",
    "ANTHROPIC_API_KEY",
    "BRIDGE_TOKEN",
)


@pytest.mark.timeout(120)
class TestWorker:
    @pytest.mark.parametrize("name", REQUIRED)
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_configuration_fails_before_routing(
        self, worker: httpx.Client, name: str, value: Any
    ) -> None:
        case = {"body": BODY, "settings": {name: value}}
        if value is None:
            case["missing"] = [name]
        result = worker.post("/ingress", json=case).raise_for_status().json()
        assert result["status"] == 503
        assert result["room"] is None

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://app.band.ai",
            "https://user:pass@app.band.ai",
            "https://",
            "garbage",
            "https://app.band.ai:invalid",
            "https://app.band.ai:99999",
        ],
    )
    def test_invalid_endpoint_fails_closed(
        self, worker: httpx.Client, endpoint: str
    ) -> None:
        result = (
            worker.post(
                "/ingress", json={"body": BODY, "settings": {"BAND_REST_URL": endpoint}}
            )
            .raise_for_status()
            .json()
        )
        assert result["status"] == 503
        assert result["room"] is None

    @pytest.mark.parametrize(
        "case,status",
        [
            ({"auth": ""}, 401),
            ({"auth": "Bearer private-wrong"}, 401),
            ({"auth": "Bearer x"}, 401),
            ({"body": "{"}, 400),
            ({"body": "[]"}, 400),
            ({"body": "null"}, 400),
            ({"path": "/ping"}, 404),
            ({"method": "GET"}, 404),
            *[
                ({"body": json.dumps({**ENVELOPE, "room_id": value})}, 400)
                for value in (None, "", " ", 123, [], {})
            ],
            *[
                ({"body": json.dumps({**ENVELOPE, "agent_id": value})}, 400)
                for value in ("other", None, 4)
            ],
            *[
                ({"body": json.dumps({**ENVELOPE, "event_type": value})}, 400)
                for value in (None, 4, "", " ")
            ],
        ],
    )
    def test_invalid_ingress_never_routes(
        self, worker: httpx.Client, case: dict[str, Any], status: int
    ) -> None:
        result = (
            worker.post("/ingress", json={"body": BODY, **case})
            .raise_for_status()
            .json()
        )
        assert result["status"] == status
        assert result["room"] is None

    def test_routes_original_body_to_exact_room(self, worker: httpx.Client) -> None:
        result = worker.post("/ingress", json={"body": BODY}).raise_for_status().json()
        assert result == {
            "status": 201,
            "body": BODY,
            "room": ENVELOPE["room_id"],
            "forwarded_body": BODY,
            "version": "version",
        }

    @pytest.mark.parametrize(
        "scenario", ["lifecycle", "crash", "deadline", "interceptor"]
    )
    def test_runtime_boundaries(self, worker: httpx.Client, scenario: str) -> None:
        response = worker.get("/" + scenario)
        assert response.status_code == 200, response.text
        assert response.json()["passed"] is True
