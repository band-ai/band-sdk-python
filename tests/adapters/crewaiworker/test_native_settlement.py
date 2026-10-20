"""Native SDK work must settle before a shared resource can be released."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.crewaiworker.adapters import AdapterKind, adapter_case
from tests.adapters.crewaiworker.kit import native_case
from tests.adapters.waiting import UNTIL_TIMEOUT_S


class TestNativeSettlement:
    @pytest.mark.parametrize("kind", ["crewai", "flow"])
    async def test_adapter_cleanup_retains_native_work_across_waiter_cancellation(
        self, kind: AdapterKind
    ) -> None:
        async with asyncio.timeout(UNTIL_TIMEOUT_S), adapter_case(kind) as case:
            case.consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await case.consumer
            await case.work.native_entered.wait()
            assert case.work.tool_settled.is_set()
            assert case.work.resource_open_at_settlement

            started = asyncio.Event()

            async def cleanup() -> None:
                started.set()
                await case.adapter.cleanup_all()

            closing = asyncio.create_task(cleanup())
            try:
                await started.wait()
                assert not closing.done()
                assert not case.work.client.is_closed
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing

                started.clear()
                closing = asyncio.create_task(cleanup())
                await started.wait()
                assert not closing.done()
                assert not case.work.client.is_closed
                case.work.native_release.set()
                await closing
            finally:
                case.work.native_release.set()
                await asyncio.gather(closing, return_exceptions=True)

            assert isinstance(case.work.late_error, InputNotAccepted)
            assert case.work.tool_calls == 1
            assert case.work.native_reply == case.expected
            assert case.work.native_finished.is_set()
            await case.work.client.aclose()
            assert case.work.client.is_closed

    async def test_cancelled_consumer_retains_sdk_and_rejects_late_tools(self) -> None:
        async with asyncio.timeout(UNTIL_TIMEOUT_S), native_case() as case:
            case.consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await case.consumer
            await case.turn.abort()
            await case.work.native_entered.wait()
            assert case.work.tool_settled.is_set()
            assert case.work.resource_open_at_settlement
            assert not case.native.done()
            assert not case.client.is_closed

            closing = asyncio.create_task(case.turn.close(cancel=True))
            try:
                assert not closing.done()
                case.work.native_release.set()
                await closing
            finally:
                case.work.native_release.set()
                await asyncio.gather(closing, return_exceptions=True)
            assert isinstance(case.work.late_error, InputNotAccepted)
            assert case.work.tool_calls == 1
            assert case.work.native_reply == case.expected
            await case.turn.close(cancel=True)
            await case.client.aclose()
            assert case.client.is_closed

    async def test_cancelled_cleanup_waiter_can_retry_the_same_native_work(
        self,
    ) -> None:
        async with asyncio.timeout(UNTIL_TIMEOUT_S), native_case() as case:
            case.consumer.cancel()
            await asyncio.gather(case.consumer, return_exceptions=True)
            await case.turn.abort()
            started = asyncio.Event()

            async def close() -> None:
                started.set()
                await case.turn.close(cancel=True)

            closing = asyncio.create_task(close())
            try:
                await started.wait()
                await case.work.native_entered.wait()
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
                assert not case.native.done()
                assert not case.client.is_closed
            finally:
                case.work.native_release.set()
                await asyncio.gather(closing, return_exceptions=True)
                await case.native
            await case.turn.close(cancel=True)
            assert case.work.native_reply == case.expected
            await case.client.aclose()
            assert case.client.is_closed
