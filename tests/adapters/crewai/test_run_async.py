from __future__ import annotations

import importlib
import sys


class TestRunAsync:
    def test_run_async_with_running_loop(self, crewai_mocks):

        module = importlib.import_module("band.integrations.crewai.runtime")
        module._nest_asyncio_applied = False

        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        async def test_coro() -> str:
            return "result"

        result = module.run_async(test_coro())

        assert result == "result"
        nest_mock.apply.assert_called_once()

    def test_run_async_without_running_loop(self, crewai_mocks):

        module = importlib.import_module("band.integrations.crewai.runtime")
        module._nest_asyncio_applied = True

        nest_mock = sys.modules["nest_asyncio"]
        nest_mock.reset_mock()

        async def test_coro() -> str:
            return "result"

        result = module.run_async(test_coro())

        assert result == "result"
