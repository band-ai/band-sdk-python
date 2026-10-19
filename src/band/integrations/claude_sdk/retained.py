"""Keep SDK version checks and unconfirmed CLI children under transport custody."""

from __future__ import annotations

import asyncio

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from band.core.exceptions import CleanupIncompleteError


class RetainedCLITransport(SubprocessCLITransport):
    """Retain an unconfirmed SDK child for the next close attempt."""

    _version_check: asyncio.Task[None] | None = None
    _exit_confirmation_deferred = False

    async def _check_claude_version(self) -> None:
        # The SDK's version child exists only inside this coroutine. Cancelling
        # its final wait would abandon that child before the main process starts.
        self._version_check = asyncio.create_task(super()._check_claude_version())
        await asyncio.shield(self._version_check)

    def _defer_exit_confirmation(self) -> None:
        """Let an outer child custodian perform physical-exit confirmation."""
        self._exit_confirmation_deferred = True

    async def close(self) -> None:
        if self._version_check is not None:
            await asyncio.shield(self._version_check)
        process = self._process
        try:
            await super().close()
        finally:
            # SDK close can forget its child after the final wait times out.
            # Keep that handle until a later close confirms physical exit.
            if process is not None and process.returncode is None:
                self._process = process
        if (
            process is not None
            and process.returncode is None
            and not self._exit_confirmation_deferred
        ):
            raise CleanupIncompleteError("Claude CLI process exit is unconfirmed")
