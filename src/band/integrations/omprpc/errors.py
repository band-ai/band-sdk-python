"""Native OMP RPC failure types."""

from __future__ import annotations

from band.integrations.ompwire import OmpResponse


class OmpRpcError(RuntimeError):
    """An unsuccessful command response; the transport remains usable."""

    def __init__(self, command: str, error: str, *, code: str | None = None) -> None:
        self.command = command
        self.error = error
        self.code = code
        super().__init__(f"OMP {command}: {error}")


class OmpProcessError(RuntimeError):
    """The OMP process or its stdio transport is no longer usable."""


BACKLOG_EXCEEDED = "OMP event callback backlog exceeded"
CALLBACKS_UNFINISHED = "OMP callbacks did not finish after settlement"


class OmpProtocolError(OmpProcessError):
    """An invalid or incorrectly correlated native RPC frame."""


class OmpPromptError(RuntimeError):
    """The prompt or its final background continuation failed or was aborted."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        super().__init__(message)


def command_error(response: OmpResponse) -> OmpRpcError:
    return OmpRpcError(
        response.command,
        response.error or "Command failed",
        code=response.code,
    )
