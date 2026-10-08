"""Band tools as Parlant tools.

The tool modules import Parlant at the top, so they load only through
``create_parlant_tools``: ``band.adapters.parlant`` imports this module and
must stay importable in a venv without the parlant extra.
"""

from __future__ import annotations

import logging
from typing import Any

from band.core.types import AdapterFeatures
from band.integrations.parlant.sessiontools import (
    get_current_tools,
    get_session_tools,
    set_current_tools,
    set_session_tools,
)

__all__ = [
    "create_parlant_tools",
    "get_current_tools",
    "get_session_tools",
    "set_current_tools",
    "set_session_tools",
]

logger = logging.getLogger(__name__)


def create_parlant_tools(features: AdapterFeatures | None = None) -> list[Any]:
    """Parlant ``ToolEntry`` objects for the Band tools *features* enables.

    Each tool resolves the current room's ``AgentToolsProtocol`` from its
    Parlant session at call time (see ``sessiontools``).
    """
    try:
        import parlant.sdk  # noqa: F401, PLC0415 -- parlant extra, absent from the standard dev venv
    except ImportError:
        logger.warning("Parlant SDK not installed, skipping tool creation")
        return []

    from band.integrations.parlant.bandtool import (  # noqa: PLC0415 -- imports parlant; see module docstring
        build_band_tool,
    )
    from band.integrations.parlant.bandtools import (  # noqa: PLC0415 -- imports parlant; see module docstring
        band_tool_specs,
    )

    return [build_band_tool(spec) for spec in band_tool_specs(features)]
