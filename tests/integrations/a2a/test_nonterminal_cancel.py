"""Non-terminal cancel evidence latches the room unavailable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.types import TaskState

from band.core.exceptions import ProviderUnavailableError
from band.core.turn_lifecycle import PROVIDER_UNAVAILABLE_MESSAGE, BackendTurns
from band.integrations.a2a.adapter import A2AAdapter, A2ATaskRun
from band.testing import FakeAgentTools, failure_reports
from tests.framework_conformance.turnprobes import ROOM_ID


class TestNonterminalCancel:
    async def test_working_cancel_evidence_latches_unavailable(self) -> None:
        owner = SimpleNamespace()
        owner._finalize_task = lambda *_args: None
        owner.backend_turns = BackendTurns(
            lambda run: A2AAdapter._cancel_task(owner, run),  # type: ignore[arg-type]
            provider="a2a",
            requires_confirmation=True,
        )
        run = A2ATaskRun(
            client=SimpleNamespace(
                cancel_task=AsyncMock(
                    return_value=SimpleNamespace(
                        status=SimpleNamespace(state=TaskState.TASK_STATE_WORKING)
                    )
                )
            ),
            room_id=ROOM_ID,
            task_id="task-1",
        )
        tools = FakeAgentTools()
        async with owner.backend_turns.running(ROOM_ID, run):
            owner.backend_turns.cancel(ROOM_ID)
        await owner.backend_turns.settled(ROOM_ID)
        assert owner.backend_turns.unavailable(ROOM_ID)
        with pytest.raises(ProviderUnavailableError):
            await owner.backend_turns.require_admission(ROOM_ID, tools)
        assert failure_reports(tools) == [("a2a", PROVIDER_UNAVAILABLE_MESSAGE)]
