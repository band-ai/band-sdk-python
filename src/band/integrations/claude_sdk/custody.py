"""Physical custody of a room's Claude CLI until its exit is confirmed."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk._internal.transport import Transport

CHILD_EXIT_TIMEOUT_SECONDS = 5.0
"""How long a child the native close left running may take to die once killed."""


class ClaudeProcessCloseError(RuntimeError):
    """The Claude CLI child could not be confirmed exited; its transport keeps
    the child for a later ``close()`` to retry."""


class ConfirmedCloseTransport(Transport):
    """The SDK's subprocess transport, holding the CLI child until it is reaped.

    The native ``close()`` can return after its waits time out with the child
    still running, and it forgets the child either way, so neither its return
    nor a cleared handle proves exit. This wrapper keeps the child it saw
    spawned, kills and reaps one that a normal close left running, and keeps
    the child (raising ``ClaudeProcessCloseError``) when that cannot be
    confirmed. A close that raised or was cancelled also keeps the child, so a
    later ``close()`` retries.
    """

    def __init__(self, inner: Transport) -> None:
        self._inner = inner
        self._child: Any = None

    @property
    def close_pending(self) -> bool:
        """Whether a child is held whose exit is not yet confirmed."""
        return self._child is not None

    def _observe_child(self) -> None:
        if (process := getattr(self._inner, "_process", None)) is not None:
            self._child = process

    async def connect(self) -> None:
        try:
            await self._inner.connect()
        finally:
            self._observe_child()

    async def write(self, data: str) -> None:
        await self._inner.write(data)

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        return self._inner.read_messages()

    def is_ready(self) -> bool:
        return self._inner.is_ready()

    async def end_input(self) -> None:
        await self._inner.end_input()

    async def close(self) -> None:
        self._observe_child()
        # Accept the child before asking an inner transport to defer its own
        # exit assertion; this owner can then kill and reap a forgotten child.
        if self._child is not None and (
            defer := getattr(self._inner, "_defer_exit_confirmation", None)
        ):
            defer()
        await self._inner.close()
        await self._confirm_exit()

    async def _confirm_exit(self) -> None:
        """Kill and reap a child the native close left running, within
        ``CHILD_EXIT_TIMEOUT_SECONDS``; otherwise keep it and raise."""
        child = self._child
        if child is None:
            return
        if child.returncode is None:
            try:
                with contextlib.suppress(ProcessLookupError):
                    child.kill()
                async with asyncio.timeout(CHILD_EXIT_TIMEOUT_SECONDS):
                    await child.wait()
            except Exception as error:  # noqa: BLE001 -- unconfirmed exit, whatever the cause
                raise ClaudeProcessCloseError(
                    "the Claude CLI process could not be confirmed exited"
                    f" ({type(error).__name__})"
                ) from None
        self._child = None
