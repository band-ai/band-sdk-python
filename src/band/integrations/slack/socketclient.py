"""Socket Mode reconnects retain borrowed WebClient resources until settled."""

from __future__ import annotations

import asyncio
from typing import Any

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.web.async_client import AsyncWebClient


class SettlingSocketModeClient(SocketModeClient):
    def __init__(self, *, app_token: str, web_client: AsyncWebClient) -> None:
        self._reconnects: set[asyncio.Task[Any]] = set()
        super().__init__(app_token=app_token, web_client=web_client)

    async def connect_to_new_endpoint(self, force: bool = False) -> None:
        if self.closed:
            return
        task = asyncio.current_task()
        assert task is not None
        self._reconnects.add(task)
        try:
            await super().connect_to_new_endpoint(force=force)
        finally:
            self._reconnects.discard(task)

    async def close(self) -> None:
        tasks = self._owned_tasks()
        await super().close()
        tasks.update(self._owned_tasks())
        for task in tasks:
            if not isinstance(task, asyncio.Task) or not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _owned_tasks(self) -> set[asyncio.Future[Any]]:
        tasks: set[asyncio.Future[Any]] = set(self._reconnects)
        tasks.update(
            task
            for task in (
                self.message_processor,
                self.message_receiver,
                self.current_session_monitor,
            )
            if task is not None
        )
        return tasks
