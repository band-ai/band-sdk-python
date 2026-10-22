"""Explicit process boundaries for lifecycle tests."""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Any

import pytest
from hypothesis import strategies as st

from band.contracts.models import ProcessOptions
from band.contracts.process import ManagedProcess


class Child:
    def __init__(self) -> None:
        self.pid = 123
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self.exit = asyncio.get_running_loop().create_future()
        self.terminated = asyncio.Event()
        self.terminate_count = 0
        self.kill_count = 0
        self.auto_exit = True

    def ready(self) -> None:
        self.stdout.feed_data(
            json.dumps({"event": "ready", "url": "http://127.0.0.1:8000"}).encode()
            + b"\n"
        )

    def finish(self, status: int = 0) -> None:
        if self.returncode is None:
            self.returncode = status
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.exit.set_result(status)

    def terminate(self) -> None:
        self.terminate_count += 1
        self.terminated.set()
        if self.auto_exit:
            self.finish()

    def kill(self) -> None:
        self.kill_count += 1
        self.finish(-9)

    async def wait(self) -> int:
        return await asyncio.shield(self.exit)


class Spawn:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.child = Child()
        self.calls = 0

    async def __call__(self, *args: str, **kwargs: Any) -> Child:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return self.child

    def owner(self) -> ManagedProcess:
        return ManagedProcess(
            ("python", "-m", "example"), options=ProcessOptions(), ready_event="ready"
        )


class Step(StrEnum):
    START = "start"
    CLOSE = "close"
    EXIT = "exit"


sequences = st.lists(st.sampled_from(list(Step)), min_size=1, max_size=12)


async def play(steps: list[Step]) -> None:
    spawn = Spawn()
    spawn.child.ready()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(asyncio, "create_subprocess_exec", spawn)
        owner = spawn.owner()
        closed = False
        for step in steps:
            match step:
                case Step.START if not closed:
                    await owner.start()
                    assert owner.running
                case Step.CLOSE:
                    await owner.aclose()
                    closed = True
                case Step.EXIT if spawn.calls:
                    spawn.child.finish()
                    await owner.aclose()
                    closed = True
                case Step.START | Step.EXIT:
                    pass
        await owner.aclose()
        assert not owner.running
        assert spawn.calls <= 1
        if spawn.calls:
            assert spawn.child.returncode is not None
