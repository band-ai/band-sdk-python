# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[google_adk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Google ADK agent example.

This is the simplest way to create a Band agent using the Google Agent
Development Kit (ADK) with Gemini models. The adapter handles conversation
history, tool calling, and platform integration via ADK's built-in Runner.

Requires Band credentials plus one of:
    - GOOGLE_API_KEY or GOOGLE_GENAI_API_KEY environment variable (Gemini Developer API)
    - gcloud CLI with Application Default Credentials (Vertex AI):
        gcloud auth application-default login
        export GOOGLE_GENAI_USE_VERTEXAI=true
        export GOOGLE_CLOUD_PROJECT=your-project-id

Run with:
    uv run examples/google_adk/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv


from band import Agent, configure_logging
from band.adapters import GoogleADKAdapter

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    # Create adapter with Google ADK settings
    adapter = GoogleADKAdapter(
        model="gemini-2.5-flash",
        custom_section="You are a helpful assistant. Be concise and friendly.",
    )

    logger.info("Starting Google ADK agent...")
    async with Agent.from_config(
        "google_adk_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
