"""An adapter's cleanup deadline counts only its own overrun as unfinished."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import RoomRetentionConfig
from band.core.retention.cleanup import cleanup_deadline, finish_by

pytestmark = pytest.mark.timeout(10)


async def _raise_timeout() -> None:
    raise TimeoutError("backend's own timeout")


class TestCleanupDeadline:
    @pytest.mark.looptime
    async def test_a_step_past_the_deadline_is_unfinished_cleanup(self) -> None:
        deadline = cleanup_deadline(RoomRetentionConfig(transition_timeout_s=1.0))

        with pytest.raises(CleanupIncompleteError, match="MCP close still running"):
            await finish_by(deadline, asyncio.sleep(5), what="MCP close")

    async def test_a_timeout_the_cleanup_raised_itself_is_its_failure(self) -> None:
        deadline = cleanup_deadline(RoomRetentionConfig(transition_timeout_s=10.0))

        with pytest.raises(TimeoutError, match="backend's own timeout"):
            await finish_by(deadline, _raise_timeout(), what="MCP close")
