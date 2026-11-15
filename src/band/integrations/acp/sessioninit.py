"""Observe shared session initialization even after its last caller leaves."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SessionSetupObserver:
    """Retrieve terminal errors and report abandoned failures once."""

    def __init__(self, task: asyncio.Task[str]) -> None:
        self.task = task
        self.consumed = False
        self.abandoned = False
        self._reported = False
        task.add_done_callback(self._settled)

    def consume(self) -> str:
        """Transfer the outcome to the live caller's normal error path."""
        self.consumed = True
        return self.task.result()

    def abandon(self) -> None:
        """Report even when completion raced the last caller's cancellation."""
        self.abandoned = True
        if self.task.done():
            self._settled(self.task)

    def _settled(self, task: asyncio.Task[str]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if (
            error is not None
            and self.abandoned
            and not self.consumed
            and not self._reported
        ):
            self._reported = True
            # Resolver exceptions may contain credentials or private workspace data.
            logger.warning(
                "Abandoned ACP session setup failed (%s)", type(error).__name__
            )
