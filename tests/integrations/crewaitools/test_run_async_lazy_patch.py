"""RunAsyncLazyPatch behavior at the CrewAI tool boundary."""

from __future__ import annotations


class TestRunAsyncLazyPatch:
    def test_apply_lazy_only_once(self, runtime_mod, crewai_mocks):
        runtime_mod._nest_asyncio_applied = False
        crewai_mocks.reset_mock()

        async def coro_value() -> str:
            return "ok"

        runtime_mod.run_async(coro_value())
        runtime_mod.run_async(coro_value())
        runtime_mod.run_async(coro_value())

        # nest_asyncio.apply should have been called exactly once across
        # multiple run_async invocations (the lazy patch).
        assert crewai_mocks.apply.call_count == 1
