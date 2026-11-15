"""A stopped Parlant turn reopens its room only once all of its engine work ended."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_sdk_core import backend_stop_unconfirmed_message

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from band.adapters.parlant.adapter import PROVIDER
from band.core.exceptions import InputNotAccepted
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.parlant.engine import EngineSession, Holdout
from tests.adapters.parlant.helpers import SESSION_ID, RecordingBackgroundTasks
from tests.adapters.waiting import spin

pytestmark = [pytest.mark.usefixtures("parlant_sessions"), pytest.mark.looptime]

ROOM_ID = "room-123"
TEARDOWN_BUDGET_SECONDS = 7.0
UNCONFIRMED_REPORT = {
    "provider": PROVIDER,
    "code": None,
    "message": backend_stop_unconfirmed_message(),
    "detail": None,
}

StartAdapter = Callable[..., Awaitable[ParlantAdapter]]
RunTurn = Callable[..., Awaitable[None]]


class TestParlantStopConfirmation:
    @staticmethod
    def engine_answers_the_post(
        mock_app: MagicMock, engine: EngineSession
    ) -> AsyncMock:
        """Start the engine's work when the customer message posts, as Parlant does."""

        async def post(**_: object) -> MagicMock:
            await engine.start()
            return MagicMock(offset=1)

        mock_app.sessions.create_customer_message = AsyncMock(side_effect=post)
        return mock_app.sessions.create_customer_message

    @staticmethod
    def no_engine_work(mock_app: MagicMock) -> AsyncMock:
        mock_app.sessions.create_customer_message = AsyncMock(
            return_value=MagicMock(offset=1)
        )
        return mock_app.sessions.create_customer_message

    @staticmethod
    def stop_unconfirmed(adapter: ParlantAdapter) -> bool:
        return adapter.backend_turns.unconfirmed_handle(ROOM_ID) is not None

    @pytest.mark.parametrize(
        ("parent", "preamble", "body"),
        [(True, None, None), (False, True, None), (False, None, True)],
        ids=["parent", "late-preamble", "plugin-body"],
    )
    async def test_resistant_work_blocks_admission_until_it_ends(
        self,
        start_adapter: StartAdapter,
        run_turn: RunTurn,
        mock_app: MagicMock,
        mock_tools: FakeAgentTools,
        background_tasks: RecordingBackgroundTasks,
        parent: bool,
        preamble: bool | None,
        body: bool | None,
    ) -> None:
        engine = EngineSession(
            background_tasks,
            SESSION_ID,
            parent=Holdout(resists=parent),
            preamble=None if preamble is None else Holdout(resists=preamble),
            body=None if body is None else Holdout(resists=body),
        )
        first_post = self.engine_answers_the_post(mock_app, engine)
        adapter = await start_adapter()

        await run_turn(adapter)

        assert self.stop_unconfirmed(adapter)
        with pytest.raises(InputNotAccepted) as blocked:
            await run_turn(adapter)
        assert first_post.await_count == 1
        assert reported_failures(mock_tools) == [UNCONFIRMED_REPORT]

        await engine.finish()
        assert blocked.value.ready is not None
        await blocked.value.ready
        assert not self.stop_unconfirmed(adapter)
        reopened_post = self.no_engine_work(mock_app)
        await run_turn(adapter)
        assert reopened_post.await_count == 1

    async def test_every_kind_of_work_must_end_before_the_stop_is_confirmed(
        self,
        start_adapter: StartAdapter,
        run_turn: RunTurn,
        mock_app: MagicMock,
        background_tasks: RecordingBackgroundTasks,
    ) -> None:
        engine = EngineSession(
            background_tasks,
            SESSION_ID,
            parent=Holdout(resists=True),
            preamble=Holdout(resists=True),
            body=Holdout(resists=True),
        )
        self.engine_answers_the_post(mock_app, engine)
        adapter = await start_adapter()
        await run_turn(adapter)
        assert engine.parent_task and engine.preamble_task and engine.body_task
        assert engine.preamble and engine.body

        engine.parent.release.set()
        await engine.parent_task
        await spin()
        assert self.stop_unconfirmed(adapter)

        # Only after the parent ended could the settlement see its late child.
        await engine.preamble.cancel_seen.wait()
        engine.preamble.release.set()
        await engine.preamble_task
        await spin()
        assert self.stop_unconfirmed(adapter)

        engine.body.release.set()
        await engine.body_task
        await spin()
        assert not self.stop_unconfirmed(adapter)

    async def test_a_drained_stop_reopens_the_room_for_a_new_turn(
        self,
        start_adapter: StartAdapter,
        run_turn: RunTurn,
        mock_app: MagicMock,
        mock_tools: FakeAgentTools,
        background_tasks: RecordingBackgroundTasks,
    ) -> None:
        engine = EngineSession(
            background_tasks,
            SESSION_ID,
            parent=Holdout(resists=False),
            preamble=Holdout(resists=False),
            body=Holdout(resists=False),
        )
        self.engine_answers_the_post(mock_app, engine)
        adapter = await start_adapter()

        await run_turn(adapter)

        assert engine.parent_task and engine.preamble_task and engine.body_task
        assert engine.parent_task.cancelled()
        assert engine.preamble_task.cancelled()
        assert engine.body_task.cancelled()
        assert not self.stop_unconfirmed(adapter)
        reopened_post = self.no_engine_work(mock_app)
        await run_turn(adapter)
        assert reopened_post.await_count == 1
        assert reported_failures(mock_tools) == []

    async def test_teardown_gives_up_waiting_without_confirming_resistant_work(
        self,
        start_adapter: StartAdapter,
        run_turn: RunTurn,
        mock_app: MagicMock,
        background_tasks: RecordingBackgroundTasks,
    ) -> None:
        engine = EngineSession(
            background_tasks, SESSION_ID, parent=Holdout(resists=True)
        )
        self.engine_answers_the_post(mock_app, engine)
        adapter = await start_adapter(
            ParlantAdapterConfig(
                response_timeout=0.05,
                response_poll=0.01,
                transition_timeout_s=TEARDOWN_BUDGET_SECONDS,
            )
        )
        await run_turn(adapter)

        await adapter.cleanup_all()

        assert self.stop_unconfirmed(adapter)
        await engine.finish()
        await spin()
        assert not self.stop_unconfirmed(adapter)
