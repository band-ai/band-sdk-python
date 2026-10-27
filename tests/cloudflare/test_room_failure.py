"""A room fails requests closed on receiver failure and recovers on the next request."""

from __future__ import annotations

from typing import Any

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import (
    ABORTED_PROBE,
    COLD_START,
    CRASH_ERROR,
    DESTROY_ERROR,
    UPSTREAM_FAILURE,
    Call,
    Scenario,
)

BOOTED = [*COLD_START, Call.INSPECT]


def deadline_log(constants: dict[str, Any]) -> str:
    """The unavailable log for a readiness deadline whose last probe was aborted."""
    readiness = constants["readiness_exceeded"] % ABORTED_PROBE
    return constants["receiver_unavailable_log"] % readiness


@pytest.mark.timeout(120)
class TestRoomFailure:
    def test_crash_during_boot_fails_every_waiter_then_the_next_request_restarts(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.CRASH_DURING_BOOT) == {
            "statuses": [503, 503, 503],
            "recovered": 201,
            "calls": [*COLD_START, Call.DESTROY, *BOOTED, Call.FORWARD],
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
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.DEADLINE) == {
            "status": 503,
            "calls": [*COLD_START, Call.DESTROY],
            "running": False,
            "fired_early": [False],
            "deadline_timers_cancelled": [True],
            "logs": [
                ["INFO", constants["receiver_stopped_log"]],
                ["ERROR", deadline_log(constants)],
            ],
        }

    def test_readiness_deadline_keeps_an_already_running_container(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.DEADLINE_WHILE_RUNNING) == {
            "status": 503,
            "calls": [Call.IDLE, Call.MONITOR, Call.PING],
            "running": True,
            "fired_early": [False],
            "deadline_timers_cancelled": [True],
            "logs": [["ERROR", deadline_log(constants)]],
        }

    def test_failed_retries_on_a_running_container_log_its_stop_once(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.RETRIES_SHARE_MONITOR) == {
            "statuses": [503, 503, 503],
            "recovered": 201,
            "calls": [
                Call.IDLE,
                Call.MONITOR,
                Call.PING,
                Call.PING,
                Call.PING,
                *BOOTED,
                Call.FORWARD,
            ],
            "logs": [["WARNING", constants["receiver_failed_log"] % CRASH_ERROR]],
        }

    def test_cleanup_failure_keeps_the_startup_cause(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.CLEANUP_FAILURE) == {
            "status": 503,
            "calls": [*COLD_START, Call.DESTROY],
            "logs": [
                ["WARNING", constants["receiver_failed_log"] % CRASH_ERROR],
                ["WARNING", constants["cleanup_failed_log"] % DESTROY_ERROR],
                ["ERROR", constants["receiver_unavailable_log"] % CRASH_ERROR],
            ],
        }

    def test_crash_after_readiness_is_logged_and_the_next_request_restarts(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        assert runtime.run(Scenario.CRASH_AFTER_READY) == {
            "statuses": [201, 201],
            "calls": [*BOOTED, Call.FORWARD, *BOOTED, Call.FORWARD],
            "logs": [["WARNING", constants["receiver_failed_log"] % CRASH_ERROR]],
        }

    def test_forward_failure_keeps_the_container_and_redacts_secrets(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        secrets = " ".join([constants["redacted"]] * len(constants["secret"]))
        assert runtime.run(Scenario.FORWARD_FAILURE) == {
            "response": {
                "status": 503,
                "body": constants["receiver_unavailable"],
                "room_version": None,
                "image": None,
            },
            "calls": [*BOOTED, Call.FORWARD],
            "running": True,
            "logs": [
                [
                    "ERROR",
                    constants["receiver_unavailable_log"]
                    % f"Error: {secrets} {UPSTREAM_FAILURE}",
                ]
            ],
        }
