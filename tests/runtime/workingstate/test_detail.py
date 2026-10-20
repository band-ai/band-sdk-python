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

    async def test_observation_queued_behind_a_report_is_rechecked_under_the_lock(
        self,
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        reports: list[str] = []

        async def report(working: bool) -> None:
            if working:
                reports.append(reporter.activity.kind)
            if reporter.activity.kind == "tool_call":
                entered.set()
                await release.wait()

        reporter = WorkingStateReporter(report)
        owned = True
        try:
            await reporter.start()
            change = asyncio.create_task(
                reporter.update(
                    current=lambda: owned,
                    activity=AgentActivityDetail(kind="tool_call", name="Bash"),
                )
            )
            await entered.wait()
            stale = asyncio.create_task(
                reporter.update(
                    current=lambda: owned, activity=AgentActivityDetail(kind="memory")
                )
            )
            owned = False
            release.set()
            await asyncio.gather(change, stale)
        finally:
            await reporter.stop()
        assert reports == ["reasoning", "tool_call"]

    async def test_empty_tool_name_reports_unknown(self) -> None:
        reports: list[dict[str, Any]] = []

        async def report(working: bool) -> None:
            if working:
                reports.append(reporter.activity.model_dump(exclude_unset=True))

        reporter = WorkingStateReporter(report)
        try:
            await reporter.start()
            await reporter.update(
                current=lambda: True,
                activity=AgentActivityDetail(kind="tool_call", name=""),
            )
        finally:
            await reporter.stop()
        assert reports[-1] == {"kind": "tool_call", "name": "unknown"}

    async def test_observation_outside_a_cycle_reports_nothing(self) -> None:
        reports: list[bool] = []

        async def report(working: bool) -> None:
            reports.append(working)

        reporter = WorkingStateReporter(report)
        await reporter.update(
            current=lambda: True,
            activity=AgentActivityDetail(kind="tool_call", name="Bash"),
        )
        assert reports == []

    async def test_permission_wait_does_not_carry_into_the_next_cycle(self) -> None:
        reports: list[str | None] = []

        async def report(working: bool) -> None:
            reports.append(reporter.activity.kind if working else None)

        reporter = WorkingStateReporter(report)
        try:
            await reporter.start()
            await reporter.update(current=lambda: True, waiting=True)
            await reporter.stop()
            await reporter.start()
        finally:
            await reporter.stop()
        assert reports == ["reasoning", "permission", None, "reasoning", None]
