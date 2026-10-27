"""The public ingress routes only authenticated, well-formed invocations for its agent."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import (
    BODY,
    ENVELOPE,
    MODEL,
    SETTINGS,
    VERSION_ID,
    Scenario,
)

REQUIRED = (
    "BAND_AGENT_ID",
    "BAND_REST_URL",
    "BAND_API_KEY",
    "ANTHROPIC_API_KEY",
    "BRIDGE_TOKEN",
)
OPTIONAL = ("ANTHROPIC_MODEL",)
PADDED = {**SETTINGS, "ANTHROPIC_MODEL": MODEL}


@pytest.mark.timeout(120)
class TestIngress:
    def test_setting_names_match_the_worker(self, constants: dict[str, Any]) -> None:
        assert (constants["required"], constants["optional"]) == (
            list(REQUIRED),
            list(OPTIONAL),
        )

    @pytest.mark.parametrize(
        "settings,invalid",
        [
            *[
                ({name: value}, name)
                for name in REQUIRED
                for value in (None, "", "   ")
            ],
            *[
                ({"BAND_REST_URL": endpoint}, "BAND_REST_URL")
                for endpoint in (
                    "http://app.band.ai",
                    "https://user:pass@app.band.ai",
                    "https://",
                    "garbage",
                    "https://app.band.ai:invalid",
                    "https://app.band.ai:99999",
                )
            ],
            *[({name: f" {PADDED[name]} "}, name) for name in (*REQUIRED, *OPTIONAL)],
            ({"ANTHROPIC_MODEL": "   "}, "ANTHROPIC_MODEL"),
        ],
    )
    def test_invalid_setting_fails_closed_and_is_named(
        self,
        runtime: WorkerRuntime,
        constants: dict[str, Any],
        settings: dict[str, str | None],
        invalid: str,
    ) -> None:
        result = runtime.run(Scenario.INGRESS, settings=settings)
        assert result == {
            "status": 503,
            "body": constants["configuration_unavailable"],
            "room": None,
            "forwarded_body": None,
            "worker_version": None,
            "logs": [["ERROR", constants["invalid_settings_log"] % invalid]],
        }

    @pytest.mark.parametrize("model", ["", MODEL])
    def test_empty_or_named_model_routes(
        self, runtime: WorkerRuntime, model: str
    ) -> None:
        result = runtime.run(Scenario.INGRESS, settings={"ANTHROPIC_MODEL": model})
        assert (result["status"], result["room"]) == (201, ENVELOPE["room_id"])

    @pytest.mark.parametrize(
        "case,status",
        [
            ({"request": {"authorization": ""}}, 401),
            ({"request": {"authorization": "Bearer private-wrong"}}, 401),
            ({"request": {"authorization": "Bearer x"}}, 401),
            ({"request": {"body": "{"}}, 400),
            ({"request": {"body": "[]"}}, 400),
            ({"request": {"body": "null"}}, 400),
            ({"request": {"path": "/ping"}}, 404),
            ({"request": {"method": "GET"}}, 404),
            ({"request": {"method": "PROPFIND"}}, 404),
            # Combined faults pin the check order: settings, route, auth, then body.
            ({"settings": {"BAND_API_KEY": None}, "request": {"path": "/ping"}}, 503),
            ({"request": {"authorization": "", "path": "/ping"}}, 404),
            ({"request": {"authorization": "", "body": "{"}}, 401),
            *[
                ({"request": {"body": json.dumps({**ENVELOPE, "room_id": value})}}, 400)
                for value in (None, "", " ", 123, [], {})
            ],
            *[
                (
                    {"request": {"body": json.dumps({**ENVELOPE, "agent_id": value})}},
                    400,
                )
                for value in ("other", None, 4)
            ],
            *[
                (
                    {
                        "request": {
                            "body": json.dumps({**ENVELOPE, "event_type": value})
                        }
                    },
                    400,
                )
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
