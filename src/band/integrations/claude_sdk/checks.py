"""Standalone Claude host checks without changing room-session ownership."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from typing import TYPE_CHECKING, Any

from band.core.exceptions import BandConfigError, CleanupIncompleteError
from band.core.harness import PreflightResult
from band.core.simple_adapter import SimpleAdapter

if TYPE_CHECKING:
    from band.adapters.claude_sdk import ClaudeSDKAdapter


def require_server_info(info: dict[str, Any] | None) -> dict[str, Any]:
    if info is None:
        raise BandConfigError("Claude did not return initialization metadata")
    return info


async def preflight_result(initialization: Awaitable[object]) -> PreflightResult:
    try:
        await initialization
    except CleanupIncompleteError:
        raise
    except TimeoutError:
        return PreflightResult.failed(
            "Claude initialization exceeded its timeout.",
            "Check the configured CLI and host settings, or increase timeout_s.",
        )
    except Exception as error:  # noqa: BLE001 -- SDK failures are public diagnostics; never echo provider configuration
        return PreflightResult.failed(
            f"Claude initialization failed ({type(error).__name__}).",
            "Check the configured Claude CLI, credentials, environment and settings.",
        )
    return PreflightResult.passed()


def close_host_probes(
    cleanup_rooms: Callable[[ClaudeSDKAdapter], Awaitable[None]],
) -> Callable[[ClaudeSDKAdapter], Coroutine[Any, Any, None]]:
    @wraps(cleanup_rooms)
    async def cleanup(adapter: ClaudeSDKAdapter) -> None:
        failures: list[BaseException] = []
        # Room admission and self-request detection must run in the requester
        # task before an unrelated probe can wait; never detach this call.
        for close in (cleanup_rooms(adapter), SimpleAdapter.cleanup_all(adapter)):
            try:
                await close
            except BaseException as error:  # noqa: BLE001 -- attempt every cleanup without discarding cancellation
                failures.append(error)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("Claude adapter cleanup failed", failures)

    return cleanup
