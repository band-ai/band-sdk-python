"""The public ingress routes only authenticated, well-formed invocations for its agent."""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import BODY, ENVELOPE, MODEL, VERSION_ID, Scenario

REQUIRED = (
    "BAND_AGENT_ID",
    "BAND_REST_URL",
    "BAND_API_KEY",
    "ANTHROPIC_API_KEY",
    "BRIDGE_TOKEN",
)
OPTIONAL = ("ANTHROPIC_MODEL",)
CONFIGURATION_FAILURE = (503, None, [["ERROR", mock.ANY]])


@pytest.mark.timeout(120)
class TestIngress:
    def test_setting_names_match_the_worker(self, constants: dict[str, Any]) -> None:
        assert (constants["required"], constants["optional"]) == (
            list(REQUIRED),
            list(OPTIONAL),
        )

    @pytest.mark.parametrize("name", REQUIRED)
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_required_setting_fails_closed_and_is_named(
        self, runtime: WorkerRuntime, name: str, value: str | None
    ) -> None:
        result = runtime.run(Scenario.INGRESS, settings={name: value})
        assert (
            result["status"],
            result["room"],
            result["logs"],
        ) == CONFIGURATION_FAILURE
        assert result["logs"][0][1].endswith(name)

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
    def test_invalid_endpoint_fails_closed_and_is_named(
        self, runtime: WorkerRuntime, endpoint: str
    ) -> None:
        result = runtime.run(Scenario.INGRESS, settings={"BAND_REST_URL": endpoint})
        assert (
            result["status"],
            result["room"],
            result["logs"],
        ) == CONFIGURATION_FAILURE
        assert result["logs"][0][1].endswith("BAND_REST_URL")

    def test_blank_model_fails_closed_and_is_named(
        self, runtime: WorkerRuntime
    ) -> None:
        result = runtime.run(Scenario.INGRESS, settings={"ANTHROPIC_MODEL": "   "})
        assert (
            result["status"],
            result["room"],
            result["logs"],
        ) == CONFIGURATION_FAILURE
        assert result["logs"][0][1].endswith("ANTHROPIC_MODEL")

    @pytest.mark.parametrize("model", ["", MODEL])
    def test_empty_or_named_model_routes(
        self, runtime: WorkerRuntime, model: str
    ) -> None:
        result = runtime.run(Scenario.INGRESS, settings={"ANTHROPIC_MODEL": model})
        assert (result["status"], result["room"]) == (201, ENVELOPE["room_id"])

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
            ({"method": "PROPFIND"}, 404),
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
        self, runtime: WorkerRuntime, case: dict[str, Any], status: int
    ) -> None:
        result = runtime.run(Scenario.INGRESS, **case)
        assert (result["status"], result["room"]) == (status, None)

    def test_routes_original_body_to_exact_room(self, runtime: WorkerRuntime) -> None:
        assert runtime.run(Scenario.INGRESS) == {
            "status": 201,
            "body": BODY,
            "room": ENVELOPE["room_id"],
            "forwarded_body": BODY,
            "worker_version": VERSION_ID,
            "logs": [],
        }
