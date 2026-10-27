"""Values shared by the host tests and the workerd harness.

The harness loads this file as a top-level module inside workerd and the host
tests import it from the ``tests`` package, so it imports only the stdlib.
"""

from __future__ import annotations

import json
from enum import StrEnum


class Scenario(StrEnum):
    """Harness routes; each runs one claim inside workerd and returns observations."""

    CONSTANTS = "constants"
    INGRESS = "ingress"
    KEY_INJECTOR = "key-injector"
    SHARED_BOOT = "shared-boot"
    WARM_REUSE = "warm-reuse"
    PING_RETRY = "ping-retry"
    REHYDRATION = "rehydration"
    CREDENTIALS = "credentials"
    CRASH_DURING_BOOT = "crash-during-boot"
    STOP_DURING_BOOT = "stop-during-boot"
    DEADLINE = "deadline"
    DEADLINE_WHILE_RUNNING = "deadline-while-running"
    CLEANUP_FAILURE = "cleanup-failure"
    CRASH_AFTER_READY = "crash-after-ready"
    FORWARD_FAILURE = "forward-failure"


class Call(StrEnum):
    """Container operations the fake records, in the order the Worker makes them."""

    INTERCEPT = "intercept"
    START = "start"
    IDLE = "idle"
    MONITOR = "monitor"
    PING = "ping"
    FORWARD = "forward"
    DESTROY = "destroy"


COLD_START = [
    Call.INTERCEPT,
    Call.INTERCEPT,
    Call.START,
    Call.IDLE,
    Call.MONITOR,
    Call.PING,
]

BAND_HOST = "app.band.ai"
SETTINGS = {
    "BAND_AGENT_ID": "agent",
    "BAND_REST_URL": f"https://{BAND_HOST}",
    "BAND_API_KEY": "private-agent",
    "ANTHROPIC_API_KEY": "private-provider",
    "BRIDGE_TOKEN": "private-bridge",
}
VERSION_ID = "version"
IMAGE = "qualified-image"
MODEL = "model-under-test"
CRASH = "receiver crashed"
DESTROY_FAILURE = "destroy refused"
ENVELOPE = {
    "agent_id": SETTINGS["BAND_AGENT_ID"],
    "room_id": "room",
    "event_type": "message_created",
}
BODY = json.dumps(ENVELOPE)
