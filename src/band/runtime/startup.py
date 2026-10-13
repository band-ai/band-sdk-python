"""Bring an adapter up for a started agent, the same way from every entry point."""

from __future__ import annotations

import logging

from band.core.protocols import FrameworkAdapter
from band.core.simple_adapter import SimpleAdapter
from band.runtime.capabilities import prune_unsupported

logger = logging.getLogger(__name__)


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
    if isinstance(adapter, SimpleAdapter):
        await adapter.startup(
            agent_name,
            agent_description,
            features=prune_unsupported(adapter.features, feature_flags),
        )
    else:
        await adapter.on_started(agent_name, agent_description)


async def dispose_adapter(adapter: FrameworkAdapter | SimpleAdapter) -> None:
    """Run the adapter's ``cleanup_all``; a failure raises to the caller."""
    cleanup_all = getattr(adapter, "cleanup_all", None)
    if cleanup_all is not None:
        await cleanup_all()


async def release_adapter(adapter: FrameworkAdapter | SimpleAdapter) -> bool:
    """Best-effort ``dispose_adapter`` for error unwind: a failure is logged,
    never raised, so it cannot replace the error that made the caller release.

    Returns whether cleanup succeeded, so the caller can keep it pending.
    """
    try:
        await dispose_adapter(adapter)
    except Exception:
        logger.exception("Adapter cleanup_all failed")
        return False
    return True


__all__ = ["dispose_adapter", "release_adapter", "start_adapter"]
