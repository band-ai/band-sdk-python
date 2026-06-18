"""E2E test helpers.

Split by concern: ``log`` (pretty transcript), ``messaging`` (drive/observe
chat rooms), ``agent`` (agent lifecycle), ``memory`` (memory-test toolkit).
Import the public helpers straight from ``tests.e2e.helpers``; the submodules
are an implementation detail.
"""

from __future__ import annotations

from tests.e2e.helpers.agent import running_agent
from tests.e2e.helpers.log import log_banner, log_step
from tests.e2e.helpers.memory import MemoryProbe
from tests.e2e.helpers.messaging import (
    TrackingWebSocketClient,
    assert_content_contains,
    assert_no_content_contains,
    find_tool_call_in_context,
    listening_for_agent_responses,
    listening_for_room_activity,
    run_smoke_test,
    run_tool_execution_test,
    send_and_wait_for_reply,
    send_trigger_message,
)

__all__ = [
    "MemoryProbe",
    "TrackingWebSocketClient",
    "assert_content_contains",
    "assert_no_content_contains",
    "find_tool_call_in_context",
    "listening_for_agent_responses",
    "listening_for_room_activity",
    "log_banner",
    "log_step",
    "run_smoke_test",
    "run_tool_execution_test",
    "running_agent",
    "send_and_wait_for_reply",
    "send_trigger_message",
]
