"""Host control exceptions for portable-tool boundary regressions."""

from __future__ import annotations

from band_sdk_core import AgentFailure

from band.core.exceptions import (
    BandError,
    CleanupIncompleteError,
    InputNotAccepted,
    RoomExecutionStoppedError,
    TerminalProviderFailure,
)


def host_control_errors() -> tuple[BandError, ...]:
    """Give each parametrized boundary its own exception instances."""
    return (
        InputNotAccepted("input not accepted"),
        RoomExecutionStoppedError("room-1"),
        CleanupIncompleteError("cleanup incomplete"),
        TerminalProviderFailure(AgentFailure("provider", "terminal")),
    )
