"""Ask surfaces job errors; cast logs them and leaves the worker alive."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.runtime.mailbox import RoomMailbox


class TestJobFailures:
    async def test_ask_propagates_the_job_error(self) -> None:
        mailbox = RoomMailbox()

        async def boom() -> None:
            raise RuntimeError("job failed")

        with pytest.raises(RuntimeError, match="job failed"):
            await mailbox.ask("room-1", boom)

        async def ok() -> str:
            return "recovered"

        assert await mailbox.ask("room-1", ok) == "recovered"
        await mailbox.close("room-1")

    async def test_cast_failure_is_logged_and_later_ask_still_runs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mailbox = RoomMailbox()
        room_id = "room-1"
        exception_text = "sensitive cast details"
        done = asyncio.Event()

        async def boom() -> None:
            try:
                raise RuntimeError(exception_text)
            finally:
                done.set()

        async def ok() -> str:
            return "ok"

        with caplog.at_level(logging.ERROR, logger="band.runtime.mailbox"):
            mailbox.cast(room_id, boom)
            await done.wait()
            assert await mailbox.ask(room_id, ok) == "ok"
        assert "Room mailbox cast failed with RuntimeError" in caplog.text
        assert room_id not in caplog.text
        assert exception_text not in caplog.text
        assert "Traceback" not in caplog.text
        await mailbox.close(room_id)
