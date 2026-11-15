"""A failed timing test must not leave a runnable clock task behind."""

from __future__ import annotations

import asyncio
import sys

import pytest

from band.integrations.codex.winspawn import await_returncode
from tests.adapters.waiting import spin
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestTimeControl:
    async def test_exceptional_exit_joins_the_clock_before_another_context(
        self,
    ) -> None:
        before = asyncio.all_tasks()
        failure = RuntimeError("Clock scope interrupted")
        with pytest.raises(RuntimeError) as raised:
            async with controlled_clock() as clock:
                clock.advance(1)
                raise failure
        assert raised.value is failure
        assert asyncio.all_tasks() == before
        async with controlled_clock() as clock:
            clock.advance(1)
        assert asyncio.all_tasks() == before

    async def test_native_exit_polling_restores_clock_ownership_after_failure(
        self,
    ) -> None:
        before = asyncio.all_tasks()
        failure = RuntimeError("Native I/O scope interrupted")
        with pytest.raises(RuntimeError) as raised:
            async with controlled_clock() as clock:
                process = await clock.run_io(
                    asyncio.create_subprocess_exec(
                        sys.executable,
                        "-u",
                        "-c",
                        "import sys; sys.stdin.buffer.read()",
                        stdin=asyncio.subprocess.PIPE,
                    )
                )
                try:
                    assert process.stdin is not None
                    async with clock.background(
                        await_returncode(process, float("inf"))
                    ) as exited:
                        await spin()
                        assert not exited.done()
                        process.stdin.close()
                        assert await clock.run_io(exited)
                        assert process.returncode == 0
                finally:
                    if process.returncode is None:
                        process.kill()
                    await clock.run_io(process.wait())
                raise failure
        assert raised.value is failure
        assert asyncio.all_tasks() == before
