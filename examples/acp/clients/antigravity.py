# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=7.0.0"]
# [tool.uv.sources]
# band-sdk = { path = "../../.." }
# ///
"""Connect Band rooms to Google's standalone Antigravity ACP server.

Run from this repository checkout. The inline source override installs this
source tree rather than a published wheel. Setup is in examples/acp/README.md.
"""

from __future__ import annotations

import asyncio
import logging

from antigravitysettings import Settings, build_config
from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters.antigravity_acp import AntigravityACPAdapter

logger = logging.getLogger(__name__)


def build_adapter(settings: Settings) -> AntigravityACPAdapter:
    """Build the room adapter with an explicitly supplied provider credential."""
    return AntigravityACPAdapter(build_config(settings))


async def main() -> None:
    """Run the configured Antigravity agent until interrupted."""
    load_dotenv(".env.test")
    settings = Settings()
    adapter = build_adapter(settings)
    configure_logging()

    logger.info("Starting the Antigravity ACP bridge to Band rooms")
    async with Agent.from_config("antigravity_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
