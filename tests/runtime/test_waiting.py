"""Condition polling observes completion when a delayed timer wakes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.runtime.conftest import wait_for_condition


@pytest.mark.parametrize("completed", [False, True])
async def test_condition_at_deadline_is_checked_before_timeout(completed: bool) -> None:
    now = 0.0
    ready = False
    timeout = 1.0
    loop = SimpleNamespace(time=lambda: now)

    async def resume_poll(_interval: float) -> None:
        nonlocal now, ready
        now = timeout
        ready = completed

    with (
        patch("tests.runtime.conftest.asyncio.get_running_loop", return_value=loop),
        patch("tests.runtime.conftest.asyncio.sleep", side_effect=resume_poll),
    ):
        if completed:
            await wait_for_condition(lambda: ready, timeout=timeout)
        else:
            with pytest.raises(pytest.fail.Exception, match="Timed out"):
                await wait_for_condition(lambda: ready, timeout=timeout)
