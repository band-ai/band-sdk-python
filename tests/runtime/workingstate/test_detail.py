from __future__ import annotations

import asyncio
from typing import Any

import pytest
from band_rest import AgentActivityDetail

from band.runtime.working_state import WorkingStateReporter


@pytest.mark.looptime
class TestActivityDetail:
    async def test_changes_repeat_current_detail_and_keep_boolean_callback(
        self,
    ) -> None:
        reports: list[tuple[bool, dict[str, Any] | None]] = []

        async def report(working: bool) -> None:
            reports.append(
                (
                    working,
                    reporter.activity.model_dump(exclude_unset=True)
                    if working
                    else None,
                )
            )

        reporter = WorkingStateReporter(report)
        try:
            await reporter.start()
            await reporter.update(
                current=lambda: True,
                activity=AgentActivityDetail(
                    kind="tool_call", name="Bash", arguments="private"
                ),
            )
            await reporter.update(
                current=lambda: True,
                activity=AgentActivityDetail(kind="tool_call", name="Bash"),
            )
            await asyncio.sleep(3.1)
            await reporter.update(
                current=lambda: True,
                activity=AgentActivityDetail(
                    kind="reasoning", name="private", output="private"
                ),
            )
        finally:
            await reporter.stop()
        assert reports == [
            (True, {"kind": "reasoning"}),
            (True, {"kind": "tool_call", "name": "Bash"}),
            (True, {"kind": "tool_call", "name": "Bash"}),
            (True, {"kind": "reasoning"}),
            (False, None),
        ]

    async def test_manual_wait_overrides_latest_progress_without_restoring_stale_state(
        self,
    ) -> None:
        reports: list[str] = []

        async def report(working: bool) -> None:
            if working:
                reports.append(reporter.activity.kind)

        reporter = WorkingStateReporter(report)
        try:
            await reporter.start()
            await reporter.update(
                current=lambda: True,
                activity=AgentActivityDetail(kind="tool_call", name="Read"),
            )
            await reporter.update(current=lambda: True, waiting=True)
            await reporter.update(
                current=lambda: True, activity=AgentActivityDetail(kind="reasoning")
            )
            await reporter.update(current=lambda: True, waiting=True)
            await reporter.update(
                current=lambda: True, activity=AgentActivityDetail(kind="memory")
            )
            await reporter.update(current=lambda: True, waiting=False)
        finally:
            await reporter.stop()
        assert reports == ["reasoning", "tool_call", "permission", "memory"]

    async def test_cap_prevents_detail_from_reviving_the_indicator(self) -> None:
        reports: list[bool] = []

        async def report(working: bool) -> None:
            reports.append(working)

        reporter = WorkingStateReporter(report, max_working_state_seconds=2)
        try:
            await reporter.start()
            await asyncio.sleep(2.1)
            await reporter.update(
                current=lambda: True,
                activity=AgentActivityDetail(kind="tool_call", name="Bash"),
            )
            await asyncio.sleep(3)
            assert reports == [True]
        finally:
            await reporter.stop()
        assert reports == [True, False]
