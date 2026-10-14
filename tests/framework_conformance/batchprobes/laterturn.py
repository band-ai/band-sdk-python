"""A room's second turn after a batch, shared by the per-adapter memory tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from band.core.simple_adapter import SimpleAdapter
from tests.framework_conformance.batchprobes import (
    ALICE_FIRST,
    BATCH,
    deliver,
    delivery_input,
)

NEXT_INPUT = replace(ALICE_FIRST, id="m5", content="alice-next-turn")


async def deliver_batch_then_next_turn(adapter: SimpleAdapter[Any]) -> None:
    """Deliver ``BATCH``, then ``NEXT_INPUT`` as a later turn of the same session."""
    await deliver(adapter, await delivery_input(BATCH))
    await deliver(
        adapter,
        replace(await delivery_input((NEXT_INPUT,)), is_session_bootstrap=False),
    )
