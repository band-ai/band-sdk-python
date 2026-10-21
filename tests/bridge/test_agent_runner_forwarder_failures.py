"""Agent runner forwarder failures."""

from __future__ import annotations

from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    build_runner,
    make_message_event,
)


class TestAgentRunnerForwarderFailures:
    async def test_forwarder_exception_does_not_crash_safe_handler(self) -> None:
        fwd = FakeForwarder()
        fwd.forward_side_effect = RuntimeError("network down")
        runner, _, _ = build_runner(forwarder=fwd)

        # _safe_handle_event wraps _handle_event in a logging try/except.
        await runner._safe_handle_event(make_message_event())
