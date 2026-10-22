"""Real subprocess boundaries complement the controlled lifecycle model."""

from __future__ import annotations

import sys

from band.contracts.models import ProcessOptions
from band.contracts.process import ManagedProcess
from band.contracts.wire import decode_json


class TestSubprocess:
    async def test_ready_child_is_reaped_after_explicit_close(self) -> None:
        child = ManagedProcess(
            (
                sys.executable,
                "-I",
                "-u",
                "-c",
                "import json,threading,sys;sys.stdout.write(json.dumps({'event':'ready'})+'\\n');sys.stdout.flush();threading.Event().wait()",
            ),
            options=ProcessOptions(),
            ready_event="ready",
        )
        try:
            event = await child.start()
            assert event == {"event": "ready"}
            assert child.running
        finally:
            await child.aclose()
        assert not child.running
        assert child.result is not None
        assert not child.result.forced

    async def test_finite_operator_output_is_available_after_confirmed_exit(
        self,
    ) -> None:
        child = ManagedProcess(
            (
                sys.executable,
                "-I",
                "-c",
                "import sys;sys.stdout.write('{\"replayable\":false}');sys.exit(3)",
            ),
            options=ProcessOptions(),
        )
        result = await child.run(allowed=(0, 3))
        assert decode_json(result.stdout) == {"replayable": False}
        assert result.returncode == 3
        assert not child.running
