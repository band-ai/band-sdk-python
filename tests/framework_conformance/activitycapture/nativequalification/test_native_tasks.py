"""Native lifecycle capture pairs exact resource identities across log delivery."""

from __future__ import annotations

import asyncio
import logging

import pytest

from tests.e2e.baseline.toolkit.nativetasks import NativeTaskRecord, NativeTasks

TASK_FIELD = "native_test_task"
LOGGER = "test.nativequalification"
STARTED = "running"
COMPLETED = "completed"


class TestNativeTasks:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_other_generation_terminal_cannot_finish_captured_resource(
        self,
    ) -> None:
        capture = NativeTasks(
            task_field=TASK_FIELD,
            terminal_statuses=frozenset({COMPLETED}),
            deadline_s=10,
        )
        logger = logging.getLogger(LOGGER)
        origin = {
            "room": "room",
            "generation": 1,
            "task_id": "task",
            "tool_use_id": "call",
            "run_id": None,
            "revision": 1,
        }
        with capture.observing(LOGGER):
            logger.info(
                "Task lifecycle", extra={TASK_FIELD: {**origin, "status": STARTED}}
            )
            started = await capture.started("room", since=0)
            logger.info(
                "Task lifecycle",
                extra={TASK_FIELD: {**origin, "generation": 2, "status": COMPLETED}},
            )
            logger.info(
                "Task lifecycle", extra={TASK_FIELD: {**origin, "status": COMPLETED}}
            )
            terminal = await capture.terminal(started)

        assert terminal == NativeTaskRecord(sequence=3, **origin, status=COMPLETED)
        assert capture not in logger.handlers

    @pytest.mark.asyncio(loop_scope="session")
    async def test_room_and_phase_cursors_apply_to_worker_thread_records(self) -> None:
        capture = NativeTasks(
            task_field=TASK_FIELD,
            terminal_statuses=frozenset({COMPLETED}),
            deadline_s=10,
        )
        logger = logging.getLogger(LOGGER)
        origin = {
            "room": "old-room",
            "generation": 1,
            "task_id": "task",
            "tool_use_id": "call",
            "run_id": None,
            "revision": 1,
            "status": STARTED,
        }
        with capture.observing(LOGGER):
            logger.info("Task lifecycle", extra={TASK_FIELD: origin})
            await capture.started("old-room", since=0)
            cursor = capture.cursor()
            await asyncio.to_thread(
                logger.info,
                "Task lifecycle",
                extra={TASK_FIELD: {**origin, "room": "new-room"}},
            )
            current = await capture.started("new-room", since=cursor)

        assert current == NativeTaskRecord(sequence=2, **{**origin, "room": "new-room"})
