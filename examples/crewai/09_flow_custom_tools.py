# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""CrewAI Flow custom tools example.

Demonstrates registering an adapter-level custom tool and calling it from
inside a Flow via ``get_current_flow_runtime()``.

Run with:
    uv run examples/crewai/09_flow_custom_tools.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging  # noqa: E402

from band import Agent  # noqa: E402
from band.adapters import CrewAIFlowAdapter  # noqa: E402
from band.adapters.crewai_flow import get_current_flow_runtime  # noqa: E402
from band.core.types import AdapterFeatures, Emit  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)

USER_INBOX_TEXT = """Recent emails:
- Finance asked for a Q2 budget summary by Friday.
- Sales sent updated Genpact renewal notes.
- Priya shared the draft onboarding deck.
"""


class EmailsInput(BaseModel):
    """Return the user's recent emails."""


def emails() -> str:
    return USER_INBOX_TEXT


class InboxAwareFlow:
    async def kickoff_async(
        self, inputs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        inputs = inputs or {}
        message = inputs.get("message") or {}
        content = str(message.get("content") or "").lower()
        if "email" not in content and "inbox" not in content:
            return {
                "decision": "direct_response",
                "content": "I do not need inbox context for this request.",
                "mentions": [],
            }

        runtime = get_current_flow_runtime()
        if runtime is None:
            return {
                "decision": "failed",
                "error": {
                    "code": "runtime_unavailable",
                    "message": "Flow runtime is unavailable.",
                    "retryable": False,
                },
            }

        inbox = await runtime.tools.emails()
        return {
            "decision": "direct_response",
            "content": f"I found this inbox context:\n{inbox}",
            "mentions": [],
        }


def flow_factory() -> InboxAwareFlow:
    return InboxAwareFlow()


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")
    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
    adapter = CrewAIFlowAdapter(
        flow_factory=flow_factory,
        additional_tools=[(EmailsInput, emails)],
        features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
    )

    agent = Agent.from_config(
        "crewai_flow_custom_tools",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )
    logger.info("CrewAI Flow custom tools agent starting")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
