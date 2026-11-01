"""Owned loopback work and native-reader diagnostics for activity qualification."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.e2e.baseline.timeouts import SLOW_TURN_DEADLINE_S
from tests.e2e.baseline.toolkit.nativetasks import NativeTasks
from tests.e2e.baseline.toolkit.resultexchange import ResultExchange


@pytest.fixture
async def result_exchange() -> AsyncIterator[ResultExchange]:
    async with ResultExchange(deadline_s=SLOW_TURN_DEADLINE_S).running() as exchange:
        yield exchange


@pytest.fixture
async def claude_native_tasks() -> AsyncIterator[NativeTasks]:
    # Other dependency lanes collect this file without the Claude SDK extra.
    from band.integrations.claude_sdk import activity  # noqa: PLC0415

    capture = NativeTasks(
        task_field=activity.NATIVE_TASK_LIFECYCLE,
        terminal_statuses=activity.TERMINAL_TASK_STATUSES,
        deadline_s=SLOW_TURN_DEADLINE_S,
    )
    with capture.observing(activity):
        yield capture
