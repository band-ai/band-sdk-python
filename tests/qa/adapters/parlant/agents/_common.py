"""Shared launcher for Parlant expanded QA agents.

Each expanded scenario script (memory, full, contacts_*) is a thin wrapper that
calls :func:`run_parlant_agent` with the right features / contact strategy. The
agent persona stays minimal — the Band platform contract and the (feature-gated)
tool set are installed by ParlantAdapter as an always-on guideline.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

# repo-root/examples and examples/parlant on the path (for setup_logging).
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "..", "..", "examples"))
sys.path.insert(
    0, os.path.join(_HERE, "..", "..", "..", "..", "..", "examples", "parlant")
)

import parlant.sdk as p  # noqa: E402
from setup_logging import setup_logging  # noqa: E402
from band import Agent  # noqa: E402
from band.adapters import ParlantAdapter  # noqa: E402
from band.core.types import AdapterFeatures  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)


async def run_parlant_agent(
    config_key: str,
    *,
    features: AdapterFeatures,
    description: str,
    contact_config: object | None = None,
) -> None:
    """Start a Parlant-backed Band agent for an expanded QA scenario."""
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")
    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    async with p.Server(nlp_service=p.NLPServices.openai) as server:
        parlant_agent = await server.create_agent(
            name="QA-Parlant-Expanded",
            description=description,
        )
        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
            features=features,
        )
        kwargs: dict[str, object] = {
            "adapter": adapter,
            "ws_url": ws_url,
            "rest_url": rest_url,
        }
        if contact_config is not None:
            kwargs["contact_config"] = contact_config

        agent = Agent.from_config(config_key, **kwargs)
        logger.info("Starting Parlant expanded agent (config_key=%s)...", config_key)
        await agent.run()
