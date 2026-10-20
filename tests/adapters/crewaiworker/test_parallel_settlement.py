"""SDK-managed parallel branches keep resources until they finish."""

from __future__ import annotations

import asyncio

import pytest
from crewai.hooks import InterceptionPoint, get_hooks
from crewai.hooks.contexts import StepContext
from crewai.hooks.dispatch import get_scoped_hooks, scoped_hooks

from tests.adapters.crewaiworker.parallel import (
    BranchKind,
    UnrelatedFlow,
    parallel_case,
)
from tests.adapters.waiting import UNTIL_TIMEOUT_S


class TestParallelSettlement:
    @pytest.mark.parametrize("kind", ["starts", "listeners", "nested"])
    async def test_cancelled_turn_joins_sibling_across_cleanup_retry(
        self, kind: BranchKind
    ) -> None:
        hooks = get_hooks(InterceptionPoint.PRE_STEP)
        async with asyncio.timeout(UNTIL_TIMEOUT_S), parallel_case(kind) as case:
            case.consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await case.consumer
            await case.work.tool_finished.wait()
            entered = asyncio.Event()

            async def close() -> None:
                entered.set()
                await case.adapter.cleanup_all()
                await case.work.client.aclose()

            closing = asyncio.create_task(close())
            try:
                await entered.wait()
                assert not closing.done()
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
                assert not case.work.client.is_closed
                entered.clear()
                closing = asyncio.create_task(close())
                await entered.wait()
                assert not closing.done()
                case.work.release.set()
                await closing
                assert case.work.replies == [case.expected]
                assert case.work.client.is_closed
            finally:
                case.work.release.set()
                await asyncio.gather(closing, return_exceptions=True)
        assert get_hooks(InterceptionPoint.PRE_STEP) == hooks

    async def test_distinct_and_unrelated_flows_do_not_delay_one_another(self) -> None:
        hooks = get_hooks(InterceptionPoint.PRE_STEP)
        unrelated = UnrelatedFlow()
        unrelated_task = asyncio.create_task(unrelated.kickoff_async())
        try:
            async with (
                asyncio.timeout(UNTIL_TIMEOUT_S),
                parallel_case("starts") as first,
                parallel_case("listeners") as second,
            ):
                await unrelated.entered.wait()
                first.consumer.cancel()
                second.consumer.cancel()
                await asyncio.gather(
                    first.consumer, second.consumer, return_exceptions=True
                )
                first.work.release.set()
                await first.adapter.cleanup_all()
                assert first.work.replies == [first.expected]
                assert second.work.replies == []
                assert not unrelated_task.done()
                second.work.release.set()
                await second.adapter.cleanup_all()
                assert second.work.replies == [second.expected]
        finally:
            unrelated.release.set()
            await unrelated_task
        assert get_hooks(InterceptionPoint.PRE_STEP) == hooks

    async def test_branch_exception_still_joins_sibling_and_unregisters(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hooks = get_hooks(InterceptionPoint.PRE_STEP)
        async with asyncio.timeout(UNTIL_TIMEOUT_S), parallel_case("starts") as case:
            case.work.fail.set()
            await case.work.tool_finished.wait()
            assert not case.consumer.done()
            case.work.release.set()
            await case.consumer
            await case.adapter.cleanup_all()
            assert case.work.replies == [case.expected]
            assert "RuntimeError: Resource read failed" in caplog.text
        assert get_hooks(InterceptionPoint.PRE_STEP) == hooks

    async def test_inherited_scoped_hooks_run_and_are_restored(self) -> None:
        existing_hooks = get_scoped_hooks(InterceptionPoint.PRE_STEP)
        observed: list[str] = []
        completed: list[str] = []

        def observe(context: StepContext) -> None:
            observed.append(str(context.step_name))

        def observe_completion(context: StepContext) -> None:
            completed.append(str(context.step_name))

        with scoped_hooks(
            {
                InterceptionPoint.PRE_STEP: [observe],
                InterceptionPoint.POST_STEP: [observe_completion],
            }
        ):
            async with (
                asyncio.timeout(UNTIL_TIMEOUT_S),
                parallel_case("nested") as case,
            ):
                case.consumer.cancel()
                await asyncio.gather(case.consumer, return_exceptions=True)
                case.work.release.set()
                await case.adapter.cleanup_all()
                assert case.work.replies == [case.expected]
            assert get_scoped_hooks(InterceptionPoint.PRE_STEP) == [observe]
        assert sorted(observed) == ["child", "sibling", "tool"]
        assert completed == ["sibling"]
        assert get_scoped_hooks(InterceptionPoint.PRE_STEP) == existing_hooks
