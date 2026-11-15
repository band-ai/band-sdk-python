"""Compaction pairs remain session-scoped across foreground callback changes."""

from __future__ import annotations

import pytest
from copilot.generated.session_events import (
    SessionCompactionCompleteData,
    SessionCompactionStartData,
)

from band.integrations.copilot_sdk.activity import SessionActivity
from band.runtime.activity import MEMORY
from band.testing import FakeAgentTools
from band.testing.activity import SHOWN_MEMORY, SHOWN_REASONING, ActivityRecorder
from tests.adapters.copilot_sdk.fakes import FakeCopilotSession


class TestNativeCompaction:
    @pytest.mark.parametrize("success", [False, True], ids=["error", "success"])
    async def test_actual_native_pair_clears_memory_for_either_outcome(
        self,
        activity_session: tuple[FakeCopilotSession, SessionActivity],
        success: bool,
    ) -> None:
        session, activity = activity_session
        recorder = ActivityRecorder()
        activity.bind(recorder.observing(FakeAgentTools()))
        session.turn_events = [SessionCompactionStartData()]
        await session.send_and_wait("native start")
        await recorder.wait_for_detail(MEMORY)
        session.turn_events = [SessionCompactionCompleteData(success=success)]
        await session.send_and_wait("native completion")
        await recorder.next_end()
        assert recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]
        await activity.close()
        assert session.handlers == []

    async def test_no_id_overlap_never_lends_old_compaction_to_new_tools(
        self, activity_session: tuple[FakeCopilotSession, SessionActivity]
    ) -> None:
        session, activity = activity_session
        old, current = ActivityRecorder(), ActivityRecorder()
        original = old.observing(FakeAgentTools())
        activity.bind(original)
        session.turn_events = [SessionCompactionStartData()]
        await session.send_and_wait("old interval")
        await old.wait_for_detail(MEMORY)
        activity.retire(original)
        activity.bind(current.observing(FakeAgentTools()))
        session.turn_events = [
            SessionCompactionStartData(),
            SessionCompactionCompleteData(success=True),
            SessionCompactionCompleteData(success=True),
            SessionCompactionStartData(),
        ]
        await session.send_and_wait("overlap then fresh interval")
        await current.wait_for_detail(MEMORY)
        assert old.events == [SHOWN_MEMORY, SHOWN_REASONING]
        assert current.events == [SHOWN_MEMORY]
        session.turn_events = [SessionCompactionCompleteData(success=True)]
        await session.send_and_wait("fresh completion")
        await current.next_end()
        assert current.events == [SHOWN_MEMORY, SHOWN_REASONING]
