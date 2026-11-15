"""A Google ADK room's later turns replay what its earlier batch delivered."""

from __future__ import annotations

import pytest

from tests.framework_conformance.batchprobes import (
    BATCH,
    EARLIER_ANSWER,
    EARLIER_QUESTION,
    FINAL_TEXT,
)
from tests.framework_conformance.batchprobes.googleadk import recording_adapter
from tests.framework_conformance.batchprobes.laterturn import (
    deliver_batch_then_next_turn,
)

pytest.importorskip("google.adk", reason="google-adk not installed")

CONTEXT_OPEN = "[Previous conversation context]"
CONTEXT_CLOSE = "[End of previous context]"


def replayed_transcript(model_request: str) -> list[str]:
    """The lines ADK's request replays as the conversation so far."""
    _, _, after_open = model_request.partition(CONTEXT_OPEN)
    transcript, _, _ = after_open.partition(CONTEXT_CLOSE)
    return transcript.strip().splitlines()


class TestGoogleADKBatchHistory:
    async def test_a_later_turn_replays_each_original_once_in_order_before_the_reply(
        self,
    ) -> None:
        async with recording_adapter(FINAL_TEXT) as (adapter, requests):
            await deliver_batch_then_next_turn(adapter)

        assert replayed_transcript(requests[-1]) == [
            f"[Alice]: {EARLIER_QUESTION}",
            f"[Helper]: {EARLIER_ANSWER}",
            *(f"[{o.sender_name}]: {o.content}" for o in BATCH),
            f"[Agent]: {FINAL_TEXT}",
        ]
