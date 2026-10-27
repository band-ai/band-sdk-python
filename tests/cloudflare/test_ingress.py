"""The public ingress routes only authenticated, well-formed invocations for its agent."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import (
    BODY,
    CREATED,
    ENVELOPE,
    MODEL,
    MODEL_SETTING,
    SETTINGS,
    VERSION_ID,
    Scenario,
)

REQUIRED = tuple(SETTINGS)
OPTIONAL = (MODEL_SETTING,)
PADDED = {**SETTINGS, MODEL_SETTING: MODEL}
UNAUTHORIZED = ("unauthorized", [("WARNING", "unauthorized_log")])
BAD_ENVELOPE = ("invalid_envelope", [("WARNING", "invalid_envelope_log")])
BAD_ROUTING = ("invalid_routing", [("WARNING", "invalid_routing_log")])
NOT_FOUND = ("not_found", [])


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
            ({MODEL_SETTING: "   "}, MODEL_SETTING),
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
        "case,status,rejection",
        [
            ({"request": {"authorization": ""}}, 401, UNAUTHORIZED),
            ({"request": {"authorization": "Bearer private-wrong"}}, 401, UNAUTHORIZED),
            ({"request": {"authorization": "Bearer x"}}, 401, UNAUTHORIZED),
            ({"request": {"body": "{"}}, 400, BAD_ENVELOPE),
            ({"request": {"body": "[]"}}, 400, BAD_ENVELOPE),
            ({"request": {"body": "null"}}, 400, BAD_ENVELOPE),
            ({"request": {"path": "/ping"}}, 404, NOT_FOUND),
            ({"request": {"method": "GET"}}, 404, NOT_FOUND),
            ({"request": {"method": "PROPFIND"}}, 404, NOT_FOUND),
            # Combined faults pin the check order: settings, route, auth, then body.
            (
                {"settings": {"BAND_API_KEY": None}, "request": {"path": "/ping"}},
                503,
                (
                    "configuration_unavailable",
                    [("ERROR", "invalid_settings_log", "BAND_API_KEY")],
                ),
            ),
            ({"request": {"authorization": "", "path": "/ping"}}, 404, NOT_FOUND),
            ({"request": {"authorization": "", "body": "{"}}, 401, UNAUTHORIZED),
            *[
                (
                    {"request": {"body": json.dumps({**ENVELOPE, field: value})}},
                    400,
                    BAD_ROUTING,
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
        rejection: tuple[str, list[tuple[str, ...]]],
    ) -> None:
        body, logs = rejection
        assert runtime.run(Scenario.INGRESS, **case) == {
            "status": status,
            "body": constants[body],
            "room": None,
            "forwarded_body": None,
            "worker_version": None,
            "logs": [
                [level, constants[message] % tuple(args)]
                for level, message, *args in logs
            ],
        }

    @pytest.mark.parametrize(
        "settings", [{}, {MODEL_SETTING: ""}, {MODEL_SETTING: MODEL}]
    )
    def test_routes_original_body_to_exact_room(
        self, runtime: WorkerRuntime, settings: dict[str, str]
    ) -> None:
        assert runtime.run(Scenario.INGRESS, settings=settings) == {
            "status": CREATED,
            "body": BODY,
            "room": ENVELOPE["room_id"],
            "forwarded_body": BODY,
            "worker_version": VERSION_ID,
            "logs": [],
        }
