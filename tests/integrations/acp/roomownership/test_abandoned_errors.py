"""Abandoned initializer errors are observed without exposing credentials."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.adapters.hold import Hold
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter
from tests.integrations.acp.acp_toolkit.mcpresolver import (
    SECRET,
    FailingResolver,
)


class TestAbandonedErrors:
    @pytest.mark.parametrize("successor", [False, True])
    async def test_late_failure_is_reported_once_and_does_not_fail_successor(
        self, successor: bool, caplog: pytest.LogCaptureFixture
    ) -> None:
        callback = FailingResolver()
        agent = FakeACPAgent(http=True).will_say("recovered")
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        unhandled: list[dict[str, Any]] = []
        loop.set_exception_handler(lambda loop, context: unhandled.append(context))
        try:
            async with acp_adapter(agent, resolve_mcp_servers=callback) as acp:
                first = asyncio.create_task(acp.send("first"))
                following = None
                try:
                    await callback.entered.wait()
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    await callback.settling.wait()
                    room = acp.room()
                    if successor:
                        following = asyncio.create_task(acp.send("next"))
                        await until(
                            lambda: bool(room.retained._waiting),
                            failure="successor not waiting for abandonment",
                        )
                    callback.settle.set()
                    await until(
                        lambda: (
                            room.setup_observer is not None
                            and room.setup_observer.abandoned
                            and room.setup_observer.task.done()
                            if following is None
                            else following.done()
                        ),
                        failure="setup did not settle",
                    )
                    if following is not None:
                        assert (await following).thoughts == ["recovered"]
                    else:
                        assert agent.sessions == []
                finally:
                    callback.settle.set()
                    await asyncio.gather(first, return_exceptions=True)
                    if following is not None:
                        await following
            failures = [
                record
                for record in caplog.records
                if record.name == "band.integrations.acp.sessioninit"
            ]
            assert len(failures) == 1
            assert "LookupError" in failures[0].getMessage()
            assert SECRET not in caplog.text
            assert unhandled == []
        finally:
            loop.set_exception_handler(previous)

    async def test_completion_racing_last_waiter_cancellation_is_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        gate = Hold()

        async def resolver(workspace: str) -> list[Any]:
            await gate.wait()
            raise LookupError(SECRET)

        async with acp_adapter(
            FakeACPAgent(http=True), resolve_mcp_servers=resolver
        ) as acp:
            turn = asyncio.create_task(acp.send("racing"))
            try:
                await gate.arrived()
                setup = acp.room().session_setup
                assert setup is not None
                setup.add_done_callback(lambda task: turn.cancel())
                gate.released.set()
                with pytest.raises(asyncio.CancelledError):
                    await turn
            finally:
                gate.released.set()
                await asyncio.gather(turn, return_exceptions=True)
        assert caplog.text.count("Abandoned ACP session setup failed") == 1
        assert SECRET not in caplog.text
