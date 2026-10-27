"""A room boots its container once, reuses it while it runs, and isolates credentials."""

from __future__ import annotations

from typing import Any

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import (
    BAND_HOST,
    BODY,
    COLD_START,
    CREATED,
    IMAGE,
    MODEL,
    MODEL_SETTING,
    SETTINGS,
    VERSION_ID,
    Call,
    Scenario,
)

SERVED = {"status": CREATED, "body": BODY, "room_version": VERSION_ID, "image": IMAGE}


@pytest.mark.timeout(120)
class TestRoomBoot:
    def test_concurrent_requests_share_one_cold_start(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.SHARED_BOOT) == {
            "calls_while_booting": [Call.INTERCEPT],
            "calls": [
                *COLD_START,
                Call.INSPECT,
                Call.FORWARD,
                Call.FORWARD,
                Call.FORWARD,
            ],
            "responses": [SERVED] * 3,
        }

    def test_warm_request_skips_readiness(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.WARM_REUSE) == {
            "calls": [*COLD_START, Call.INSPECT, Call.FORWARD, Call.FORWARD],
            "idle_timeouts": [constants["idle_ms"]],
            "statuses": [CREATED, CREATED],
        }

    def test_readiness_retries_until_the_receiver_is_healthy(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.PING_RETRY) == {
            "calls": [*COLD_START, Call.PING, Call.PING, Call.INSPECT, Call.FORWARD],
            "status": CREATED,
        }

    def test_rehydrated_room_reuses_the_running_container(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.REHYDRATION) == {
            "calls": [Call.IDLE, Call.MONITOR, Call.PING, Call.INSPECT, Call.FORWARD],
            "idle_timeouts": [constants["idle_ms"]],
            "status": CREATED,
        }

    @pytest.mark.parametrize(
        "model,forwarded",
        [(None, {}), ("", {}), (MODEL, {MODEL_SETTING: MODEL})],
    )
    def test_container_gets_placeholders_while_injectors_hold_the_keys(
        self,
        runtime: WorkerRuntime,
        constants: dict[str, Any],
        model: str | None,
        forwarded: dict[str, str],
    ) -> None:
        placeholder = constants["proxy_key"]
        result = runtime.run(Scenario.CREDENTIALS, settings={MODEL_SETTING: model})
        assert result == {
            "status": CREATED,
            "options": {
                "image": IMAGE,
                "instance": constants["instance"],
                "enableInternet": False,
                "env": {
                    "BAND_AGENT_ID": SETTINGS["BAND_AGENT_ID"],
                    "BAND_REST_URL": SETTINGS["BAND_REST_URL"],
                    "BAND_API_KEY": placeholder,
                    "ANTHROPIC_API_KEY": placeholder,
                    constants["port_setting"]: str(constants["port"]),
                    **forwarded,
                },
            },
            "keys_by_host": {
                BAND_HOST: SETTINGS["BAND_API_KEY"],
                constants["anthropic_host"]: SETTINGS["ANTHROPIC_API_KEY"],
            },
            "forwarded": [
                {
                    "url": f"{constants['http']}//{constants['container_host']}"
                    f"{constants['invocations_path']}",
                    "authorization": None,
                }
            ],
        }

    def test_unreported_image_is_tagged_unknown(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.UNREPORTED_IMAGE) == {
            **SERVED,
            "image": constants["unknown_image"],
        }
