"""Deterministic guard for the baseline E2E toolkit's usage-record parsing.

``UsageRecord.from_event`` reads a durable ``ChatMessage`` fetched from the
Human messages API. band-client-rest 0.0.38 retyped ``ChatMessage.metadata``
from a plain dict to a frozen, ``.get()``-less ``ChatMessageMetadata`` model,
which is exactly what a real usage event now carries — a live platform isn't
needed to prove the toolkit still parses it.

This runs in the fast unit lane (no live platform), unlike the ``tests/e2e``
tree which is skipped unless ``E2E_TESTS_ENABLED``.
"""

from __future__ import annotations

from band_rest import ChatMessage
from band_rest.types.chat_message_metadata import ChatMessageMetadata

from band.core.types import USAGE_METADATA_KEY
from tests.e2e.baseline.toolkit.observations.usage import UsageRecord


def _usage_message(**band_usage: object) -> ChatMessage:
    return ChatMessage(
        id="task-1",
        content="usage",
        sender_id="agent-1",
        sender_type="Agent",
        message_type="task",
        metadata=ChatMessageMetadata(**{USAGE_METADATA_KEY: band_usage}),
    )


class TestUsageRecordFromEvent:
    def test_reads_token_counts_from_model_metadata(self) -> None:
        message = _usage_message(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

        record = UsageRecord.from_event(message)

        assert record is not None
        assert record.input_tokens == 100
        assert record.output_tokens == 20
        assert record.cache_read_tokens == 5
        assert record.cache_write_tokens == 3
        assert record.raw is message

    def test_none_for_lifecycle_task_without_usage_key(self) -> None:
        message = ChatMessage(
            id="task-2",
            content="lifecycle",
            sender_id="agent-1",
            sender_type="Agent",
            message_type="task",
            metadata=ChatMessageMetadata(claude_sdk_session_id="sess-1"),
        )

        assert UsageRecord.from_event(message) is None
