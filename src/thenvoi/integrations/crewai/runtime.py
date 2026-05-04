"""CrewAI sync-to-async runtime bridge.

Important: This module uses nest_asyncio to enable nested event loops, which is
required because CrewAI tools are synchronous but need to call async platform
methods. The nest_asyncio.apply() call is IRREVERSIBLE and affects the entire
Python process — all event loops will allow nesting after this is applied.
The patch is applied lazily on first tool execution, not at import time.

Extracted from src/thenvoi/adapters/crewai.py so CrewAIAdapter and
CrewAIFlowAdapter share one bridge implementation.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Coroutine, TypeVar

try:
    import nest_asyncio
except ImportError as e:  # pragma: no cover - same import guard as the adapter
    raise ImportError(
        "crewai is required for CrewAI adapter.\n"
        "Install with: pip install 'thenvoi-sdk[crewai]'\n"
        "Or: uv add crewai nest-asyncio"
    ) from e

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Module-level state for nest_asyncio patch.
# See module docstring for important notes about global process impact.
_nest_asyncio_applied = False
_nest_asyncio_lock = threading.Lock()


def _ensure_nest_asyncio() -> None:
    """Apply nest_asyncio patch lazily on first use.

    This function is thread-safe via a lock to prevent race conditions
    when multiple threads attempt to apply the patch simultaneously.

    See module docstring for important notes about global process impact.
    """
    global _nest_asyncio_applied
    if _nest_asyncio_applied:
        return

    with _nest_asyncio_lock:
        # Double-check after acquiring lock (double-checked locking pattern)
        if not _nest_asyncio_applied:
            nest_asyncio.apply()
            _nest_asyncio_applied = True
            logger.debug("Applied nest_asyncio patch for nested event loops")


def run_async(
    coro: Coroutine[Any, Any, T],
    fallback_loop: asyncio.AbstractEventLoop | None = None,
) -> T:
    """Run an async coroutine from sync context.

    CrewAI tools are synchronous but need to call async platform methods.
    With nest_asyncio applied, we can safely run coroutines even when
    an event loop is already running.

    This function handles two scenarios:
    1. An event loop is running — uses run_until_complete with nest_asyncio
    2. No event loop is running — uses asyncio.run to create one
    """
    _ensure_nest_asyncio()

    try:
        loop = asyncio.get_running_loop()
        logger.debug("Running coroutine in existing event loop via nest_asyncio")
    except RuntimeError:
        # No running event loop — prefer the adapter's main loop when available.
        # CrewAI may execute tools in worker threads, and platform clients are
        # bound to the runtime loop created during agent startup.
        if fallback_loop is not None and fallback_loop.is_running():
            logger.debug(
                "Running coroutine on fallback event loop via thread-safe submit"
            )
            future = asyncio.run_coroutine_threadsafe(coro, fallback_loop)
            return future.result()

        # No running event loop and no active fallback loop — use asyncio.run
        logger.debug("Running coroutine in new event loop via asyncio.run")
        return asyncio.run(coro)

    # Event loop is running — use run_until_complete (safe with nest_asyncio)
    return loop.run_until_complete(coro)


__all__ = ["run_async"]
