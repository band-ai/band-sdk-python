"""A Slack brain with observable work and teardown."""

from __future__ import annotations

import asyncio
from typing import Any

from tests.integrations.slack.wrappingkit import SlackReplyBrain


class GatedBrain(SlackReplyBrain):
    def __init__(self) -> None:
        super().__init__(reply=None)
        self.entered = asyncio.Event()
        self.finalizing = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cleanup_calls = 0
        self.fail_start = False

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        if self.fail_start:
            raise OSError("Inner startup failed")

    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.finalizing.set()
            await self.release.wait()
            self.finished.set()

    async def cleanup_all(self) -> None:
        self.cleanup_calls += 1
