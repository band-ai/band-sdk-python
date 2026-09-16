"""Shared test helpers for tests/platform."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from band.client.streaming import WebSocketClient
from band_sdk_core import DeadReason, SessionState


class AllTopicsJoined:
    """A container reporting every topic as present -- the default 'nothing
    failed to rejoin' fixture behavior for ``WebSocketClient.joined_topics``,
    which returns a real snapshot BandLink checks membership against."""

    def __contains__(self, topic: object) -> bool:
        return True


class AllTopicsExcept:
    """Reports every topic present except the given ones -- simulates a
    room/topic that PHX's own rejoin pass did not re-establish."""

    def __init__(self, missing: set[str]) -> None:
        self._missing = missing

    def __contains__(self, topic: object) -> bool:
        return topic not in self._missing


@pytest.fixture
def mock_ws_client():
    """Autospecced WebSocketClient for testing BandLink.

    ``spec=WebSocketClient`` (via ``create_autospec``) so a rename/removal of
    a real method surfaces as a test failure here, instead of the mock
    silently auto-fabricating whatever attribute BandLink happens to call.
    """
    ws = create_autospec(WebSocketClient, instance=True)

    # Async context manager support
    ws.__aenter__.return_value = ws
    ws.__aexit__.return_value = None

    # Documented "nothing failed to rejoin" default -- an unconfigured
    # autospec call would otherwise return a bare (truthy) MagicMock, and
    # `topic in <MagicMock>` raises TypeError rather than reading as
    # "topic present" -- _detect_room_rejoin_failures/
    # _detect_agent_topic_rejoin_failures need a real container.
    ws.joined_topics.return_value = AllTopicsJoined()

    ws.last_disconnect_reason = None

    def record_terminal_disconnect(reason):
        ws.last_disconnect_reason = reason

    ws.record_terminal_disconnect.side_effect = record_terminal_disconnect

    # Documented "every real-world supersede is terminal" default (the
    # platform hardcodes retryable=False today) -- an unconfigured autospec
    # call would otherwise return a bare MagicMock whose `.state` is not
    # SessionState.Dead, silently skipping record_terminal_disconnect in
    # _on_supersede and breaking every existing supersede test using this
    # fixture.
    ws.handle_supersede.return_value = MagicMock(
        state=SessionState.Dead,
        dead_reason=DeadReason.Classified,
        stale_reason=None,
        retry_after_s=None,
    )

    return ws


def gated_coroutine() -> tuple[
    Callable[..., Awaitable[None]], asyncio.Event, asyncio.Event
]:
    """A mock coroutine that blocks until released, so a caller can cancel
    it genuinely mid-await instead of before it ever starts.

    Returns ``(side_effect, started, release)`` — set ``side_effect`` as an
    AsyncMock's ``side_effect``, ``await started.wait()`` to know the call is
    truly in-flight, then cancel and/or ``release.set()``.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def side_effect(*args, **kwargs):
        started.set()
        await release.wait()

    return side_effect, started, release


@asynccontextmanager
async def cancelled_mid_await(
    gate: AsyncMock, call: Coroutine[Any, Any, None]
) -> AsyncIterator[None]:
    """Run ``call`` as a task, cancel it only once it has genuinely entered
    its await (gated on ``gate``, not before the call ever starts) — the one
    reusable shape behind every "cancel this call mid-flight" test.

    Hands control back once ``call`` is in-flight, so the caller's block can
    inject something else while it's still suspended (e.g. a concurrent
    ``disconnect()``) before cancellation happens. On exit: cancels, asserts
    ``CancelledError`` propagates uncaught, and clears ``gate``'s side effect
    so it reverts to ordinary mock behavior for anything after.
    """
    side_effect, started, _release = gated_coroutine()
    gate.side_effect = side_effect
    task = asyncio.create_task(call)
    await started.wait()
    try:
        yield
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.side_effect = None
