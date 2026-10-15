"""OpenCode synchronization observes readiness after a delayed poll wakes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.adapters.opencode.helpers import wait_for


class TestWaitFor:
    @pytest.mark.parametrize("wake_at", [1.0, 1.5])
    @pytest.mark.parametrize("completed", [False, True])
    async def test_condition_is_observed_before_expired_deadline(
        self, wake_at: float, completed: bool
    ) -> None:
        now = 0.0
        ready = False
        loop = SimpleNamespace(time=lambda: now)

        async def resume_poll(_interval: float) -> None:
            nonlocal now, ready
            assert now == 0.0, "A late poll must not start another wait"
            now = wake_at
            ready = completed

        with (
            patch(
                "tests.adapters.opencode.helpers.asyncio.get_running_loop",
                return_value=loop,
            ),
            patch(
                "tests.adapters.opencode.helpers.asyncio.sleep", side_effect=resume_poll
            ),
        ):
            if completed:
                await wait_for(lambda: ready, timeout_s=1.0)
            else:
                with pytest.raises(pytest.fail.Exception):
                    await wait_for(lambda: ready, timeout_s=1.0)
        assert now == wake_at
        assert ready is completed
