"""A room fails requests closed on receiver failure and recovers on the next request."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import (
    COLD_START,
    CRASH,
    DESTROY_FAILURE,
    SETTINGS,
    Call,
    Scenario,
)


@pytest.mark.timeout(120)
class TestRoomFailure:
    def test_crash_during_boot_fails_every_waiter_then_the_next_request_restarts(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.CRASH_DURING_BOOT) == {
            "statuses": [503, 503, 503],
            "recovered": 201,
            "calls": [*COLD_START, Call.DESTROY, *COLD_START, Call.FORWARD],
            "deadline_timers_cancelled": [True, True],
        }

    def test_clean_stop_during_boot_fails_the_request(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.STOP_DURING_BOOT) == {
            "status": 503,
            "calls": [*COLD_START, Call.DESTROY],
        }

    def test_readiness_deadline_destroys_the_container_it_started(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.DEADLINE) == {
            "status": 503,
            "calls": [*COLD_START, Call.DESTROY],
            "running": False,
            "deadline_timers_cancelled": [True],
        }

    def test_readiness_deadline_keeps_an_already_running_container(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.DEADLINE_WHILE_RUNNING) == {
            "status": 503,
            "calls": [Call.IDLE, Call.MONITOR, Call.PING],
            "running": True,
            "deadline_timers_cancelled": [True],
        }

    def test_cleanup_failure_keeps_the_startup_cause(
        self, runtime: WorkerRuntime
    ) -> None:
        result = runtime.run(Scenario.CLEANUP_FAILURE)
        assert result == {
            "status": 503,
            "calls": [*COLD_START, Call.DESTROY],
            "logs": [
                ["WARNING", mock.ANY],
                ["WARNING", mock.ANY],
                ["ERROR", mock.ANY],
            ],
        }
        crashed, cleanup, unavailable = (message for _, message in result["logs"])
        assert (CRASH in crashed, DESTROY_FAILURE in cleanup) == (True, True)
        assert (CRASH in unavailable, DESTROY_FAILURE in unavailable) == (True, False)

    def test_crash_after_readiness_is_logged_and_the_next_request_restarts(
        self, runtime: WorkerRuntime
    ) -> None:
        result = runtime.run(Scenario.CRASH_AFTER_READY)
        assert result == {
            "statuses": [201, 201],
            "calls": [*COLD_START, Call.FORWARD, *COLD_START, Call.FORWARD],
            "logs": [["WARNING", mock.ANY]],
        }
        assert CRASH in result["logs"][0][1]

    def test_forward_failure_keeps_the_container_and_redacts_secrets(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        result = runtime.run(Scenario.FORWARD_FAILURE)
        assert result == {
            "response": {
                "status": 503,
                "body": mock.ANY,
                "room_version": None,
                "image": None,
            },
            "calls": [*COLD_START, Call.FORWARD],
            "running": True,
            "logs": [["ERROR", mock.ANY]],
        }
        exposed = result["response"]["body"] + result["logs"][0][1]
        secrets = [SETTINGS[name] for name in constants["secret"]]
        assert [secret for secret in secrets if secret in exposed] == []
