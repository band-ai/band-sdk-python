"""Parlant expanded agent for Scenarios G (execution emit) and I (concurrent rooms).

Full-featured: memory + contacts capabilities and execution-event emission.
"""

from __future__ import annotations

import asyncio

from _common import run_parlant_agent

from band.core.types import AdapterFeatures, Capability, Emit

FULL_DESCRIPTION = """You are a full-featured assistant on the Band platform.

You have access to memory management, contact management, and all platform tools.
Use them when appropriate, and answer questions directly when no tool is needed.
"""


if __name__ == "__main__":
    asyncio.run(
        run_parlant_agent(
            "parlant_full_test",
            features=AdapterFeatures(
                capabilities={Capability.MEMORY, Capability.CONTACTS},
                emit={Emit.EXECUTION},
            ),
            description=FULL_DESCRIPTION,
        )
    )
