# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["band-sdk[nooa]>=4.0.0"]
# ///
"""
NOOA agent with a portable custom tool.

The adapter turns each ``(InputModel, handler)`` pair passed as
``additional_tools`` into an async method on ``self.room``, next to the Band
tools, with its signature and docstring rendered from the input model. Here
``OrderStatusInput`` becomes ``await self.room.orderstatus(order_id=...)``.

Arguments are validated against the input model. A call such as
``order_id="1234"`` raises ``BandToolError`` naming the broken rule, so the
model can read it, retry with ``order_id="ORD-1234"`` and send the result to
the room.

The model writes and runs Python inside this process, so run it in a container
or VM (see README.md). NOOA routes models through LiteLLM; this one reads
ANTHROPIC_API_KEY.

Run with:
    uv run examples/nooa/04_custom_tools.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from nooa.unifiedllm.registry import get_llm_client
from pydantic import BaseModel, Field

from band import Agent, configure_logging
from band.adapters import NooaAdapter, NooaRoomAgent

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)

ORDERS = {"ORD-1234": "shipped", "ORD-5678": "processing"}


class OrderStatusInput(BaseModel):
    """Look up the shipping status of an order."""

    order_id: str = Field(
        pattern=r"^ORD-\d{4}$",
        description="The order id, always 'ORD-' followed by four digits",
    )


def order_status(args: OrderStatusInput) -> dict[str, str]:
    """Return the order's status, or 'unknown' for an order that does not exist."""
    return {"order_id": args.order_id, "status": ORDERS.get(args.order_id, "unknown")}


class OrderDesk(NooaRoomAgent, llm=get_llm_client("anthropic/claude-sonnet-5-5")):
    """You are OrderDesk, a concise order-support assistant.

    Look orders up with `self.room.orderstatus(...)` before answering. If a
    call raises `BandToolError`, read the message, fix the arguments and try
    again. Reply in the room with what you found.
    """


async def main() -> None:
    load_dotenv()

    adapter = NooaAdapter(
        agent=OrderDesk,
        additional_tools=[(OrderStatusInput, order_status)],
    )

    logger.info("Starting NOOA order desk...")
    async with Agent.from_config("nooa_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
