"""A stand-in for the work Parlant's engine runs for one session's response.

The engine answers from a parent ``process-session`` task, may leave a
preamble child that outlives it, and runs plugin tool bodies on their own
tasks. ``EngineSession`` reproduces those three kinds of work so a stop can
be held at each boundary; the adapter's own discovery and join logic runs
unmodified against it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from band.adapters.parlant.messages import process_session_tag
from band.core.turn_lifecycle import TaskRole, TurnTask
from band.integrations.parlant.sessiontools import (
    ENGINE_ORIGIN,
    capture_engine_origin,
)
from tests.adapters.parlant.helpers import RecordingBackgroundTasks


class Holdout:
    """Work that, once cancelled, either ends or keeps running until released."""

    def __init__(self, *, resists: bool) -> None:
        self.resists = resists
        self.entered = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self) -> None:
        self.entered.set()
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            if not self.resists:
                raise
            await self.release.wait()


class AlphaEngine:
    """Shaped like Parlant's engine, so its child carries the qualname the
    adapter finds a preamble task by."""

    def _get_preamble_task(self, context: Any, work: Holdout) -> asyncio.Task[None]:
        async def preamble_task() -> None:
            # Referenced so the frame keeps it, as the engine's own child does.
            assert context is not None
            await work.run()

        return asyncio.create_task(preamble_task())


class EngineSession:
    """The parent, preamble and plugin body of one session's response."""

    def __init__(
        self,
        background_tasks: RecordingBackgroundTasks,
        session_id: str,
        *,
        parent: Holdout,
        preamble: Holdout | None = None,
        body: Holdout | None = None,
    ) -> None:
        self._background_tasks = background_tasks
        self._session_id = session_id
        self.parent = parent
        self.preamble = preamble
        self.body = body
        self.parent_task: asyncio.Task[None] | None = None
        self.preamble_task: asyncio.Task[None] | None = None
        self.body_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the parent as the engine does when a customer message posts."""
        self.parent_task = await self._background_tasks.start(
            self._process(), tag=process_session_tag(self._session_id)
        )
        started = [self.parent, self.preamble, self.body]
        await asyncio.gather(
            *(work.entered.wait() for work in started if work is not None)
        )

    async def _process(self) -> None:
        context = SimpleNamespace(session=SimpleNamespace(id=self._session_id))
        await capture_engine_origin(context, None)
        origin = getattr(context, ENGINE_ORIGIN)
        if self.preamble is not None:
            self.preamble_task = AlphaEngine()._get_preamble_task(
                context, self.preamble
            )
        if self.body is not None:
            self.body_task = asyncio.create_task(self.body.run())
            origin.bodies.add(TurnTask(self.body_task, role=TaskRole.RESOURCE))
        await self.parent.run()

    async def finish(self) -> None:
        """Release every holdout and wait for the work to end."""
        for work in (self.parent, self.preamble, self.body):
            if work is not None:
                work.release.set()
        tasks = (self.parent_task, self.preamble_task, self.body_task)
        await asyncio.gather(
            *(task for task in tasks if task is not None), return_exceptions=True
        )
