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
    and no model selection to check, so it only receives ``on_started``. A
    failed start releases what the adapter acquired before re-raising.
    """
    try:
        if isinstance(adapter, SimpleAdapter):
            await adapter.startup(
                agent_name,
                agent_description,
                features=prune_unsupported(adapter.features, feature_flags),
            )
        else:
            await adapter.on_started(agent_name, agent_description)
    except BaseException:
        await release_adapter(adapter)
        raise


async def release_adapter(adapter: FrameworkAdapter | SimpleAdapter) -> None:
    """Best-effort ``cleanup_all``: a failure is logged, never raised, so it
    cannot replace the error that made the caller release."""
    cleanup_all = getattr(adapter, "cleanup_all", None)
    if cleanup_all is None:
        return
    try:
        await cleanup_all()
    except Exception:
        logger.exception("Adapter cleanup_all failed")


__all__ = ["release_adapter", "start_adapter"]
