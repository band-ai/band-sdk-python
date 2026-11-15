"""Native body diagnostics join exact resource identities across log delivery."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.integrations.agno.activity import NATIVE_BODY_LIFECYCLE, NativeBodyStatus
from tests.e2e.baseline.toolkit.agnowork import NativeBodies, NativeBodyRecord

LOGGER = "band.integrations.agno.activity"
LIFECYCLE_MESSAGE = "Native body lifecycle"


class TestNativeBodies:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_name_peer_terminal_cannot_settle_an_older_body(self) -> None:
        capture = NativeBodies(deadline_s=10)
        logger = logging.getLogger(LOGGER)
        old = {"id": "old-body", "room": "old-room", "name": "read_result"}
        newer = {"id": "new-body", "room": "new-room", "name": old["name"]}
        with capture.observing():
            logger.info(
                LIFECYCLE_MESSAGE,
                extra={
                    NATIVE_BODY_LIFECYCLE: {**old, "status": NativeBodyStatus.ADMITTED}
                },
            )
            started = await capture.admitted(old["room"], old["name"], since=0)
            logger.info(
                LIFECYCLE_MESSAGE,
                extra={
                    NATIVE_BODY_LIFECYCLE: {
                        **newer,
                        "status": NativeBodyStatus.ADMITTED,
                    }
                },
            )
            logger.info(
                LIFECYCLE_MESSAGE,
                extra={
                    NATIVE_BODY_LIFECYCLE: {**newer, "status": NativeBodyStatus.SETTLED}
                },
            )
            logger.info(
                LIFECYCLE_MESSAGE,
                extra={
                    NATIVE_BODY_LIFECYCLE: {**old, "status": NativeBodyStatus.SETTLED}
                },
            )
            terminal = await capture.settled(started)

        assert terminal == NativeBodyRecord(
            sequence=4, **old, status=NativeBodyStatus.SETTLED
        )
        assert capture not in logger.handlers

    @pytest.mark.asyncio(loop_scope="session")
    async def test_room_and_phase_cursors_apply_to_worker_thread_records(self) -> None:
        capture = NativeBodies(deadline_s=10)
        logger = logging.getLogger(LOGGER)
        previous_level = logger.level
        body = {"id": "native-body", "room": "room", "name": "read_result"}
        with capture.observing():
            logger.info(
                LIFECYCLE_MESSAGE,
                extra={
                    NATIVE_BODY_LIFECYCLE: {**body, "status": NativeBodyStatus.ADMITTED}
                },
            )
            await capture.admitted(body["room"], body["name"], since=0)
            cursor = capture.cursor()
            next_body = {**body, "id": "next-body"}
            await asyncio.to_thread(
                logger.info,
                LIFECYCLE_MESSAGE,
                extra={
                    NATIVE_BODY_LIFECYCLE: {
                        **next_body,
                        "status": NativeBodyStatus.ADMITTED,
                    }
                },
            )
            current = await capture.admitted(body["room"], body["name"], since=cursor)

        assert current == NativeBodyRecord(
            sequence=2, **next_body, status=NativeBodyStatus.ADMITTED
        )
        assert capture not in logger.handlers
        assert logger.level == previous_level
