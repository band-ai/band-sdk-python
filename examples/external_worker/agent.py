# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[external_worker]>=9.2.0"]
# ///
"""
External worker example.

Keeps a Band agent online while its replies come from a job that runs
elsewhere: an HTTP-triggered routine, a serverless function, a cron job. On
each message this agent POSTs a wake-up-shaped request to the worker, keeps
the room's "working" indicator on, and finishes once the worker has marked the
message processed through the Band Agent API.

Prerequisites:
    1. Add an ``external_worker_agent`` entry (agent_id, api_key) to
       agent_config.yaml.
    2. Point the adapter at your worker:

       export BAND_EXTERNAL_WORKER_TRIGGER_URL=https://worker.example/run
       export BAND_EXTERNAL_WORKER_TRIGGER_TOKEN=...   # optional Bearer token

Run with (from the repository root):
    uv run examples/external_worker/agent.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters import ExternalWorkerAdapter, ExternalWorkerAdapterConfig

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()
    config = ExternalWorkerAdapterConfig()
    if config.trigger_url is None:
        raise ValueError("Set BAND_EXTERNAL_WORKER_TRIGGER_URL to your worker's URL")

    logger.info("Nudging the external worker at %s", config.trigger_url)
    async with Agent.from_config(
        "external_worker_agent", adapter=ExternalWorkerAdapter(config)
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
