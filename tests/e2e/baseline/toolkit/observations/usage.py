"""Token-usage capture and assertions for live E2E tests.

Captures the agent-under-test's per-turn token usage. When an adapter runs with
usage reporting on (``Emit.USAGE``), each turn's aggregated usage is emitted (see
``SimpleAdapter.emit_usage``) on an accepted ``task`` event whose ``metadata``
carries the token counts under ``USAGE_METADATA_KEY`` — a dedicated ``usage``
message_type would be cleaner but the backend rejects unknown types today (see
``USAGE_EVENT_TYPE``). Those events are persisted and read back via the Human
messages API (``UserOps.list_messages``), same read-after-barrier contract as
:class:`ToolCalls`. Filtering on the metadata key is what tells a usage-bearing
task event apart from an ordinary lifecycle one, so ``capture.usage`` and
``capture.tasks`` don't collide even though both ride ``task`` events.

This reads the durable record *after* the turn completes (pair it with the
delivery-status barrier ``wait_for_processed``): the adapter emits the usage
event before its reply, and the platform marks the trigger ``processed`` only
after that reply, so once the barrier returns the turn's usage event is already
persisted and queryable.

Tests reach this through ``ReplyCapture.usage`` (see ``capture.py``), which
returns a :class:`Usage` carrying the records plus fluent assertions —
``assert_recorded`` and the L4 gate ``assert_nonzero_input_and_output``.

An adapter that cannot observe usage (server-side execution) emits nothing, so
``Usage`` comes back empty — the honest N-A, distinguishable from a real
all-zero record, which ``SimpleAdapter.emit_usage`` refuses to emit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from band_rest import ChatMessage

from band.core.types import USAGE_EVENT_TYPE, USAGE_METADATA_KEY

from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageRecord:
    """One turn's observed token usage."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    raw: ChatMessage | None = None

    @property
    def total_tokens(self) -> int:
        """Input + output tokens (raw; not cache-normalized across providers)."""
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_event(cls, message: ChatMessage) -> UsageRecord | None:
        """Build a ``UsageRecord`` from a usage-bearing event's metadata.

        Returns ``None`` for any event that does not carry usage under
        ``USAGE_METADATA_KEY`` — this is the filter that ignores ordinary
        ``task`` (lifecycle) events. Tolerant of shape drift: a non-dict
        metadata or payload yields ``None`` (not raised); missing or
        non-integer fields default to 0.
        """
        metadata = message.metadata
        if not isinstance(metadata, dict):
            return None
        payload = metadata.get(USAGE_METADATA_KEY)
        if not isinstance(payload, dict):
            return None

        def _int(key: str) -> int:
            value = payload.get(key, 0)
            return value if isinstance(value, int) else 0

        return cls(
            input_tokens=_int("input_tokens"),
            output_tokens=_int("output_tokens"),
            cache_read_tokens=_int("cache_read_tokens"),
            cache_write_tokens=_int("cache_write_tokens"),
            raw=message,
        )


class Usage(list[UsageRecord]):
    """An agent's observed per-turn token usage: a ``list[UsageRecord]`` with
    fluent assertions.

    Being a list, it iterates, indexes, and ``len()``s like one. Read it once
    (see ``Usage.read`` / ``ReplyCapture.usage``), then assert as many times as
    needed against the same snapshot.
    """

    @classmethod
    async def read(
        cls,
        user_ops: UserOps,
        room_id: str,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Usage:
        """Read a room's usage records, oldest-first.

        Lists the room's usage-carrying events (``USAGE_EVENT_TYPE``) and keeps
        those whose metadata carries usage (``from_event`` filters the rest).
        Pass ``sender_id`` to keep only one agent's usage (rooms can hold
        several agents). Call after the turn is known complete (e.g. after
        ``wait_for_processed``); tests usually reach this via
        ``ReplyCapture.usage``.

        Without ``since`` this returns every usage record in the room — the turn
        only when the capture spans a single turn. Pass ``since`` (a server
        timestamp) to exclude earlier turns when reusing a capture.
        """
        messages = await user_ops.list_messages(
            room_id, message_type=USAGE_EVENT_TYPE, since=since, limit=limit
        )
        records = cls()
        for message in messages:
            if sender_id is not None and message.sender_id != sender_id:
                continue
            record = UsageRecord.from_event(message)
            if record is not None:
                records.append(record)
        return records

    def total_input_tokens(self) -> int:
        """Sum of input tokens across the observed turns."""
        return sum(record.input_tokens for record in self)

    def total_output_tokens(self) -> int:
        """Sum of output tokens across the observed turns."""
        return sum(record.output_tokens for record in self)

    def total_tokens(self) -> int:
        """Sum of input + output tokens across the observed turns."""
        return self.total_input_tokens() + self.total_output_tokens()

    def assert_recorded(self) -> None:
        """Assert at least one usage record was observed (the adapter emitted)."""
        if not self:
            raise AssertionError(
                "expected at least one usage record, but none were observed "
                "(is the agent running with Emit.USAGE, and does its adapter "
                "support it?)"
            )

    def assert_nonzero_input_and_output(self) -> None:
        """Assert non-zero input AND output tokens across the observed turns.

        This is the L4 rehydration gate: after a restart, non-zero input tokens
        mean the rehydrated ``/context`` was re-sent to the model (history
        replay), and non-zero output tokens mean the model produced a fresh
        reply (new inference). Plain per-turn usage satisfies it — no finer
        token-provenance split is needed (and none is exposed by the framework
        APIs).
        """
        self.assert_recorded()
        total_in = self.total_input_tokens()
        total_out = self.total_output_tokens()
        if total_in <= 0 or total_out <= 0:
            raise AssertionError(
                "expected non-zero input AND output tokens (the L4 replay + "
                f"new-inference gate), but observed input={total_in}, "
                f"output={total_out}"
            )
