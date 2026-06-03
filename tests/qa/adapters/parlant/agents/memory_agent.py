"""Parlant expanded agent for Scenario E (memory tools)."""

from __future__ import annotations

import asyncio

from _common import run_parlant_agent

from band.core.types import AdapterFeatures, Capability, Emit

MEMORY_DESCRIPTION = """You are a memory-management assistant on the Band platform.

When the user asks you to store, retrieve, list, supersede, or archive memories,
use the appropriate memory tools. Always report the memory ID after storing, and
summarize the content of each memory when listing.
"""


if __name__ == "__main__":
    asyncio.run(
        run_parlant_agent(
            "parlant_memory_test",
            features=AdapterFeatures(
                capabilities={Capability.MEMORY}, emit={Emit.EXECUTION}
            ),
            description=MEMORY_DESCRIPTION,
        )
    )
