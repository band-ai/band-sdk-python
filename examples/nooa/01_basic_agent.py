# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["band-sdk[nooa]>=4.0.0"]
# ///
"""
Basic NOOA (NVIDIA-labs Object Oriented Agents) example.

A NOOA agent is a Python class: the docstring is its prompt, fields are its
state, plain methods are tools the model calls from the Python it writes, and
the adapter adds the Band tools as `self.room`. Each room gets its own
instance, so `notes` below never leaks between rooms.

The model writes and runs Python inside this process. Run this example in a
container or VM, never on a machine whose files or credentials matter.

NOOA routes models through LiteLLM; this one reads ANTHROPIC_API_KEY.

Run with:
    uv run examples/nooa/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from nooa.unifiedllm.registry import get_llm_client

from band import Agent, Emit, configure_logging
from band.adapters import NooaAdapter, NooaRoomAgent

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


class NoteTaker(NooaRoomAgent, llm=get_llm_client("anthropic/claude-sonnet-5-5")):
    """You are NoteTaker, a concise assistant that keeps notes for this room.

    When someone asks you to remember something, add it with `self.remember`.
    When asked what you know, answer from `self.notes`.
    """

    notes: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.notes = []

    def remember(self, note: str) -> int:
        """Store a note for this room and return how many notes there are."""
        self.notes.append(note)
        return len(self.notes)


async def main() -> None:
    load_dotenv()

    # Report tool calls to the room, but not per-turn token usage.
    adapter = NooaAdapter(agent=NoteTaker, emit=Emit.TOOL_CALLS)

    logger.info("Starting NOOA agent...")
    async with Agent.from_config("nooa_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
