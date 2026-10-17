"""Bring an adapter up for a started agent, the same way from every entry point."""

from __future__ import annotations

import asyncio
import logging

from band.core.exceptions import BandConfigError
from band.core.protocols import FrameworkAdapter
from band.core.simple_adapter import SimpleAdapter
from band.runtime.capabilities import prune_unsupported

logger = logging.getLogger(__name__)


def accept_adapter(
    adapter: FrameworkAdapter | SimpleAdapter, owner: object, *, exclusive: bool = False
) -> bool:
    """Seal registrations and synchronously accept explicit resource ownership."""
    if not isinstance(adapter, SimpleAdapter):
        return False
    exclusive = exclusive or adapter._owns_cleanup
    adapter._disposal.check_owner(owner, exclusive=exclusive)
    # Composition validates and claims before this infallible local commit.
    adapter._accept_cleanup(owner, exclusive=exclusive)
    adapter._disposal.accept(owner, exclusive=exclusive)
    return exclusive


def arm_adapter(adapter: FrameworkAdapter | SimpleAdapter) -> None:
    """Make framework teardown eligible immediately before startup is attempted."""
    if isinstance(adapter, SimpleAdapter):
        adapter._disposal.arm_framework()


def refuse_recursive_shutdown(adapter: FrameworkAdapter | SimpleAdapter) -> None:
    if isinstance(adapter, SimpleAdapter):
        adapter._disposal.refuse_recursive_shutdown()


async def start_adapter(
    adapter: FrameworkAdapter | SimpleAdapter,
    *,
    agent_name: str,
    agent_description: str,
    feature_flags: dict[str, bool] | None,
) -> None:
    """Start ``adapter`` for an agent whose deployment serves ``feature_flags``.

    A bare ``FrameworkAdapter`` has no ``SUPPORTED_CAPABILITIES`` to negotiate
    and no model selection to check, so it only receives ``on_started``. The
    caller owns cleanup from the moment this is called, a failed start
    included, so it can keep that cleanup pending when release fails.
    """
    arm_adapter(adapter)
    if isinstance(adapter, SimpleAdapter):
        await adapter.startup(
            agent_name,
            agent_description,
            features=prune_unsupported(adapter.features, feature_flags),
        )
    else:
        await adapter.on_started(agent_name, agent_description)


async def dispose_framework(adapter: FrameworkAdapter | SimpleAdapter) -> None:
    """Finish eligible built-ins once; direct hooks never consume registrations."""
    if isinstance(adapter, SimpleAdapter):
        await adapter._disposal.finish_framework(adapter.cleanup_all)
    else:
        cleanup_all = getattr(adapter, "cleanup_all", None)
        if cleanup_all is not None:
            await cleanup_all()


async def dispose_adapter(
    adapter: FrameworkAdapter | SimpleAdapter, *, work_settled: bool = True
) -> bool:
    """Attempt framework teardown, then resources once host work has settled."""
    if not isinstance(adapter, SimpleAdapter):
        await dispose_framework(adapter)
        return True
    state = adapter._disposal
    if adapter._owns_cleanup and state.owner is None:
        raise BandConfigError(
            "Adapter resource disposal requires ownership; start the host before disposing resources"
        )
    state.refuse_recursive_shutdown()
    state.disposing = True
    state.task = asyncio.current_task()
    try:
        await dispose_framework(adapter)
        if not work_settled:
            logger.debug("Adapter resource cleanup waits for host work to settle")
            return False
        await state.finish_resources(adapter._finalize_cleanup)
        return True
    finally:
        state.task = None


async def release_adapter(
    adapter: FrameworkAdapter | SimpleAdapter, *, work_settled: bool = True
) -> bool:
    """Best-effort ``dispose_adapter`` for error unwind: a failure is logged,
    never raised, so it cannot replace the error that made the caller release.

    Returns whether cleanup succeeded, so the caller can keep it pending.
    """
    try:
        return await dispose_adapter(adapter, work_settled=work_settled)
    except Exception:
        logger.exception("Adapter disposal failed")
        return False


__all__ = ["dispose_adapter", "release_adapter", "start_adapter"]
