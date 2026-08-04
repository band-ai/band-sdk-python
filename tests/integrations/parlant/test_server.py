"""Tests for the hang-free Parlant server lifecycle helper.

The real ``p.Server`` serve-forever ``__aexit__`` is exercised live by the
parlant baseline smoke; here a stub stands in at the ``__aenter__``/``__aexit__``
boundary to pin the helper's own contract: teardown drives the exception branch
with the sentinel, never hangs, and startup ``sys.exit`` surfaces as a
catchable error.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import band.integrations.parlant.server as server_module
from band.integrations.parlant.server import (
    ServerTeardown,
    _teardown_without_serving,
    running_parlant_server,
)


class StubServer:
    """Records the ``__aexit__`` call the helper makes."""

    def __init__(self) -> None:
        self.aexit_args: tuple[Any, Any, Any] | None = None

    async def __aexit__(self, exc_type: Any, exc_value: Any, tb: Any) -> bool:
        self.aexit_args = (exc_type, exc_value, tb)
        return False


async def test_teardown_drives_exception_branch_with_sentinel():
    """The no-serve branch is selected by handing __aexit__ a real exception."""
    server = StubServer()

    await _teardown_without_serving(server)

    assert server.aexit_args is not None
    exc_type, exc_value, _ = server.aexit_args
    assert exc_type is ServerTeardown
    assert isinstance(exc_value, ServerTeardown)


async def test_teardown_suppresses_escaping_sentinel():
    """A layer re-raising the sentinel out of __aexit__ must not hit the caller."""

    class RaisingServer(StubServer):
        async def __aexit__(self, exc_type: Any, exc_value: Any, tb: Any) -> bool:
            raise exc_value

    await _teardown_without_serving(RaisingServer())


async def test_teardown_is_bounded_when_cleanup_hangs(monkeypatch, caplog):
    """A stuck cleanup await is cancelled at the ceiling instead of hanging."""

    class HangingServer(StubServer):
        async def __aexit__(self, exc_type: Any, exc_value: Any, tb: Any) -> bool:
            await asyncio.Event().wait()
            return False

    monkeypatch.setattr(server_module, "_CLEANUP_TIMEOUT_S", 0.05)

    with caplog.at_level("WARNING"):
        await asyncio.wait_for(_teardown_without_serving(HangingServer()), timeout=5)

    assert any("cleanup did not finish" in r.getMessage() for r in caplog.records)


async def test_startup_sys_exit_surfaces_as_value_error(monkeypatch):
    """Parlant's _die (sys.exit) on bad config must not kill the host process."""
    parlant_sdk = pytest.importorskip("parlant.sdk")

    class DyingServer:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            raise SystemExit(1)

    monkeypatch.setattr(parlant_sdk, "Server", DyingServer)

    with pytest.raises(ValueError, match="failed to start"):
        async with running_parlant_server():
            pass
