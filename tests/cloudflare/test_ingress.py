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

    @pytest.mark.parametrize(
        "case,status,body,invalid",
        [
            ({"request": {"authorization": ""}}, 401, "unauthorized", []),
            (
                {"request": {"authorization": "Bearer private-wrong"}},
                401,
                "unauthorized",
                [],
            ),
            ({"request": {"authorization": "Bearer x"}}, 401, "unauthorized", []),
            ({"request": {"body": "{"}}, 400, "invalid_envelope", []),
            ({"request": {"body": "[]"}}, 400, "invalid_envelope", []),
            ({"request": {"body": "null"}}, 400, "invalid_envelope", []),
            ({"request": {"path": "/ping"}}, 404, "not_found", []),
            ({"request": {"method": "GET"}}, 404, "not_found", []),
            ({"request": {"method": "PROPFIND"}}, 404, "not_found", []),
            # Combined faults pin the check order: settings, route, auth, then body.
            (
                {"settings": {"BAND_API_KEY": None}, "request": {"path": "/ping"}},
                503,
                "configuration_unavailable",
                ["BAND_API_KEY"],
            ),
            (
                {"request": {"authorization": "", "path": "/ping"}},
                404,
                "not_found",
                [],
            ),
            (
                {"request": {"authorization": "", "body": "{"}},
                401,
                "unauthorized",
                [],
            ),
            *[
                (
                    {"request": {"body": json.dumps({**ENVELOPE, field: value})}},
                    400,
                    "invalid_routing",
                    [],
                )
                for field, values in (
                    ("room_id", (None, "", " ", 123, [], {})),
                    ("agent_id", ("other", None, 4)),
                    ("event_type", (None, 4, "", " ")),
                )
                for value in values
            ],
        ],
    )
    def test_invalid_ingress_is_rejected_without_routing_or_leaking(
        self,
        runtime: WorkerRuntime,
        constants: dict[str, Any],
        case: dict[str, Any],
        status: int,
        body: str,
        invalid: list[str],
    ) -> None:
        assert runtime.run(Scenario.INGRESS, **case) == {
            "status": status,
            "body": constants[body],
            "room": None,
            "forwarded_body": None,
            "worker_version": None,
            "logs": [
                ["ERROR", constants["invalid_settings_log"] % name] for name in invalid
            ],
        }

    @pytest.mark.parametrize(
        "settings", [{}, {"ANTHROPIC_MODEL": ""}, {"ANTHROPIC_MODEL": MODEL}]
    )
    def test_routes_original_body_to_exact_room(
        self, runtime: WorkerRuntime, settings: dict[str, str]
    ) -> None:
        assert runtime.run(Scenario.INGRESS, settings=settings) == {
            "status": 201,
            "body": BODY,
            "room": ENVELOPE["room_id"],
            "forwarded_body": BODY,
            "worker_version": VERSION_ID,
            "logs": [],
        }
