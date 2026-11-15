from __future__ import annotations

from typing import Any

import pytest
from agno.run import RunStatus
from agno.run.agent import RunErrorEvent, RunOutput

from band.adapters.agno import (
    AgnoRunError,
)
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.core.types import Emit
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.agno.adapter.kit import (
    _msg,
)


class TestRunFailureReporting:
    async def test_emits_generic_error_event_and_reraises(
        self, make_started_adapter, tools
    ):
        adapter, agent = await make_started_adapter()
        agent.arun.side_effect = RuntimeError("db dsn leaked: secret-token")

        with pytest.raises(RuntimeError):
            await adapter.on_message(
                _msg("room-A", "hi"),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-A",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
        # The exception text (which can carry secrets) must not leak to the room.
        assert "secret-token" not in failures[0]["message"]
        assert failures[0]["provider"] == "agno"
        # A plain RuntimeError isn't a swallowed Agno run status -- no code.
        assert failures[0]["code"] is None

    async def test_error_status_run_is_raised_and_reported(
        self, make_started_adapter, tools
    ):
        # Agno swallows run exceptions (model/API failures included) and
        # returns a normal-looking RunOutput with status=error. The adapter
        # must surface it as a failure — otherwise the runtime marks the
        # message processed and the turn dies silently with no reply.
        adapter, _agent = await make_started_adapter(
            response=RunOutput(
                content="api dsn leaked: secret-token", status=RunStatus.error
            )
        )

        with pytest.raises(AgnoRunError):
            await adapter.on_message(
                _msg("room-A", "hi"),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-A",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert "secret-token" not in failures[0]["message"]
        assert failures[0]["code"] == RunStatus.error.value
        # A failed turn must not be committed to the room transcript.
        assert not adapter._message_history.get("room-A")

    async def test_streaming_error_event_is_raised_and_reported(
        self, make_started_adapter, tools
    ):
        # In the streaming path a swallowed error surfaces only as a
        # RunErrorEvent — the stream never yields a final RunOutput. The
        # adapter must raise on it instead of returning None (a "successful"
        # empty turn).
        adapter, _agent = await make_started_adapter(
            emit=Emit.TOOL_CALLS,
            events=[RunErrorEvent(content="api dsn leaked: secret-token")],
        )

        with pytest.raises(AgnoRunError):
            await adapter.on_message(
                _msg("room-A", "hi"),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-A",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert "secret-token" not in failures[0]["message"]
        assert failures[0]["code"] == RunStatus.error.value
        assert not adapter._message_history.get("room-A")

    async def test_error_event_failure_does_not_mask_original(
        self, make_started_adapter
    ):
        adapter, agent = await make_started_adapter()
        agent.arun.side_effect = RuntimeError("boom")

        class _FailingEventTools(FakeAgentTools):
            async def send_event(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                raise RuntimeError("event transport down")

        # The failed error-report must not replace the original exception.
        with pytest.raises(RuntimeError, match="boom"):
            await adapter.on_message(
                _msg("room-A", "hi"),
                _FailingEventTools(),
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-A",
            )
