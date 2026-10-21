from __future__ import annotations

import asyncio

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.core.protocols import FAILURE_CODE_TIMEOUT
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import reported_failures
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    COMPUTE_BUDGET_S,
    FakeOpencodeClient,
    event_permission,
    event_session_error,
    make_platform_message,
    run_single_turn,
    tools_protocol,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock


class TestTermination:
    async def test_session_error_emits_error_event(self, make_adapter, tools) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_session_error("sess-1", "boom")]]
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        failures = reported_failures(tools)
        assert failures
        assert failures[0]["provider"] == "opencode"
        assert "boom" in failures[0]["message"].lower()

    async def test_cleanup_cancel_during_abandon_still_tears_down(
        self, make_adapter, tools
    ) -> None:
        """A CancelledError inside abandon() must still clear turn state and
        shut down the shared client -- the last-room cleanup path."""

        class CancelOnAbortClient(FakeOpencodeClient):
            async def abort_session(self, session_id: str) -> None:
                raise asyncio.CancelledError

        fake_client = CancelOnAbortClient(
            prompt_event_sequences=[[event_permission("sess-1", "perm-1")]]
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        try:
            await adapter.on_cleanup("room-1")
        except asyncio.CancelledError:
            pass

        assert fake_client.closed
        assert "room-1" not in adapter._rooms

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_turn_timeout_aborts_session_and_emits_error(
        self, make_adapter, tools
    ) -> None:
        """A turn that never reaches session.idle times out, aborts the
        OpenCode session, and reports an error instead of hanging the room."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[]],  # no events at all; the turn never finishes
        )
        adapter = make_adapter(
            fake_client, config=OpencodeAdapterConfig(turn_timeout_s=COMPUTE_BUDGET_S)
        )

        async with (
            controlled_clock() as clock,
            background(run_single_turn(adapter, tools)) as turn,
        ):
            await adapter.watcher_started.wait()
            await clock.elapse(COMPUTE_BUDGET_S - 1)
            assert fake_client.aborted_sessions == []

            await clock.elapse(2)
            await turn

        assert fake_client.aborted_sessions == ["sess-1"]
        failures = reported_failures(tools)
        assert any(
            f["provider"] == "opencode" and f["code"] == FAILURE_CODE_TIMEOUT
            for f in failures
        )
        assert any("timed out" in f["message"].lower() for f in failures)
