# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[strands]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Strands agent with framework-native tools.

``additional_tools`` accepts both custom-tool forms:

* the portable ``(InputModel, handler)`` pair shared by every Band adapter
  (see 02_custom_tools.py);
* Strands' own ``@tool``-decorated functions, shown here — the schema comes from
  the signature and docstring, so nothing is re-declared.

A tool that finishes the turn on its own (a handoff, a ticket filing) can set
``band_terminal = True``. Band then treats the turn as productive even though no
``band_send_message`` was sent, instead of reporting a dropped reply.

Run with:
    uv run examples/strands/04_native_tools.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from strands import tool
from strands.models.openai import OpenAIModel

from band import Agent, configure_logging
from band.adapters import StrandsAdapter
from band.core.types import Emit

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)

_RATES = {"EUR": 0.92, "GBP": 0.79, "JPY": 157.0}


@tool
def convert_from_usd(amount: float, currency: str) -> str:
    """Convert an amount in US dollars to EUR, GBP, or JPY."""
    rate = _RATES.get(currency.upper())
    if rate is None:
        return f"Unsupported currency {currency!r}. Supported: {sorted(_RATES)}."
    return f"{amount} USD = {round(amount * rate, 2)} {currency.upper()}"


@tool
def escalate_to_human(summary: str) -> str:
    """Hand the conversation to a human teammate with a short summary."""
    logger.info("Escalated to a human: %s", summary)
    return "Escalated. A teammate will pick this up."


# The handoff ends the turn by itself, so it counts as a terminal action.
escalate_to_human.band_terminal = True


async def main() -> None:
    load_dotenv()

    adapter = StrandsAdapter(
        model=OpenAIModel(model_id="gpt-5.4-mini"),
        custom_section=(
            "You convert currencies with the convert_from_usd tool. Escalate to a "
            "human only when the request is outside currency conversion."
        ),
        additional_tools=[convert_from_usd, escalate_to_human],
        emit=Emit.TOOL_CALLS,
    )

    logger.info("Starting Strands agent with native tools...")
    async with Agent.from_config(
        "strands_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
