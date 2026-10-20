"""Failed setup and failed test bodies release the real ACP transport."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from tests.adapters.waiting import spin
from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestHarnessCleanup:
    @pytest.mark.parametrize("failure", ["setup", "body"])
    async def test_an_exception_releases_resources_before_the_next_session(
        self, failure: str
    ) -> None:
        async with controlled_clock():
            before = asyncio.all_tasks()
            original = asyncio.open_connection
            writers: list[asyncio.StreamWriter] = []

            async def opening(
                *args: Any, **kwargs: Any
            ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
                if failure == "setup" and writers:
                    raise OSError("injected setup failure")
                reader, writer = await original(*args, **kwargs)
                writers.append(writer)
                return reader, writer

            with (
                patch("asyncio.open_connection", opening),
                pytest.raises((OSError, AssertionError), match="injected"),
            ):
                async with acp_adapter(FakeACPAgent().will_say("ok")) as session:
                    await session.send("hello")
                    raise AssertionError("injected body failure")
            assert writers and all(writer.is_closing() for writer in writers)
            await spin()
            assert asyncio.all_tasks() - before == set()
            async with acp_adapter(FakeACPAgent().will_say("ok")) as next_session:
                assert (await next_session.send("independent")).errors == []
            await spin()
            assert asyncio.all_tasks() - before == set()
