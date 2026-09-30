"""Bring an adapter up for a started agent, the same way from every entry point."""

from __future__ import annotations

from band.core.protocols import FrameworkAdapter
from band.core.simple_adapter import SimpleAdapter
from band.runtime.capabilities import prune_unsupported


async def start_adapter(
    adapter: FrameworkAdapter | SimpleAdapter,
    *,
    agent_name: str,
    agent_description: str,
    feature_flags: dict[str, bool] | None,
) -> None:
    """Start ``adapter`` for an agent whose deployment serves ``feature_flags``.

    A bare ``FrameworkAdapter`` has no ``SUPPORTED_CAPABILITIES`` to negotiate
    and no model selection to check, so it only receives ``on_started``.
    """
    if isinstance(adapter, SimpleAdapter):
        await adapter.startup(
            agent_name,
            agent_description,
            features=prune_unsupported(adapter.features, feature_flags),
        )
        return
    await adapter.on_started(agent_name, agent_description)


__all__ = ["start_adapter"]
