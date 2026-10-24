"""Adapter peer that schedules native control after an admitted decline."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from tests.runtime.scheduler.kit import DecliningAdapter


class DecliningControlAdapter(DecliningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.ready = asyncio.Event()
        self.control: Callable[[], bool | None] | None = None
        self.results: asyncio.Queue[bool | None] = asyncio.Queue()

    def after_decline(self, control: Callable[[], bool | None]) -> None:
        self.control = control
        self.ready.set()

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        consumed = await super().try_handle_control(msg, tools)
        await self.ready.wait()
        # Run after the callback returns, before its owner selects terminal intent.
        asyncio.get_running_loop().call_soon(self.apply_control)
        return consumed

    def apply_control(self) -> None:
        control, self.control = self.control, None
        if control is not None:
            self.results.put_nowait(control())
