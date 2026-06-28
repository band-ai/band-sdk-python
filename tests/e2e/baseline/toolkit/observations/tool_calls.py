"""Tool-observation capture and assertions for live E2E tests.

Captures the agent-under-test's tool calls for a turn: which tools fired, with
which arguments. When an adapter runs with execution reporting on
(``Emit.EXECUTION``), each tool invocation is recorded as a ``tool_call`` event
whose ``content`` is JSON ``{"name", "args", "tool_call_id"}``. Those events are
persisted and read back via the Human messages API (``UserOps.list_messages``).

This reads the durable record *after* the turn completes (pair it with the
delivery-status barrier ``wait_for_processed``), so it needs no live event
subscription. The read is race-free by a specific contract, worth stating
because the barrier is a *delivery-state* signal while this reads *events* via a
different path (the messages-list API filtered to ``tool_call``): the adapter
``await``s each tool-call event POST before it ever emits its reply, and the
platform only marks a message ``processed`` *after* that reply is emitted — so
once ``wait_for_processed`` returns, every tool-call event of that turn is
already persisted and queryable. A real-time path would instead route the
``event_created`` WebSocket event on the chat-room channel, which the SDK client
does not currently handle; reading the persisted events is simpler and
sufficient for asserting what fired.

Tests reach this through ``ReplyCapture.tool_calls`` (see ``capture.py``), which
returns a :class:`ToolCalls` carrying the calls plus a fluent ``assert_fired``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from band_rest import ChatMessage

from band.core.types import MessageType

from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    """One observed tool invocation: the tool name and the args it fired with."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None
    raw: ChatMessage | None = None

    @classmethod
    def from_event(cls, message: ChatMessage) -> ToolCall | None:
        """Build a ``ToolCall`` from a ``tool_call`` event's JSON content.

        Tolerant of shape drift: a non-JSON or nameless payload yields ``None``
        (logged, not raised) so a single odd event never breaks inspection.
        """
        try:
            payload = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Skipping tool_call event %s: content is not JSON", message.id
            )
            return None
        if not isinstance(payload, dict) or not payload.get("name"):
            logger.warning(
                "Skipping tool_call event %s: no tool name in payload", message.id
            )
            return None
        args = payload.get("args")
        return cls(
            name=str(payload["name"]),
            args=args if isinstance(args, dict) else {},
            tool_call_id=payload.get("tool_call_id"),
            raw=message,
        )


class ToolCalls(list[ToolCall]):
    """An agent's observed tool calls for a turn: a ``list[ToolCall]`` with fluent,
    tolerant assertions.

    Being a list, it iterates, indexes, and ``len()``s like one. Read it once (see
    ``ToolCalls.read`` / ``ReplyCapture.tool_calls``), then assert as many times as
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
    ) -> ToolCalls:
        """Read a room's tool calls, oldest-first.

        Lists the room's ``tool_call`` events and parses each into a ``ToolCall``.
        Pass ``sender_id`` to keep only one agent's calls (rooms can hold several
        agents). Call after the turn is known complete (e.g. after
        ``wait_for_processed``); tests usually reach this via
        ``ReplyCapture.tool_calls``.

        Without ``since`` this returns every tool call in the room (a lower bound
        on a single turn). Pass ``since`` (a server timestamp) to exclude earlier
        turns when reusing a capture across turns.
        """
        messages = await user_ops.list_messages(
            room_id, message_type=MessageType.TOOL_CALL, since=since, limit=limit
        )
        calls = cls()
        for message in messages:
            if sender_id is not None and message.sender_id != sender_id:
                continue
            call = ToolCall.from_event(message)
            if call is not None:
                calls.append(call)
        return calls

    def fired(self, name: str) -> bool:
        """True if any call matches ``name`` (case-insensitive)."""
        return any(call.name.lower() == name.lower() for call in self)

    def assert_fired(
        self, name: str, *, with_args: dict[str, Any] | None = None
    ) -> None:
        """Assert a tool named ``name`` fired, optionally with the expected args.

        Tolerant by design: the name matches case-insensitively, and ``with_args``
        is a *subset* check (the call may carry extra args), each value matched via
        ``_arg_matches``. It is not an exact-args assertion; agents vary phrasing
        and may pass additional fields. With ``with_args`` omitted, a name match
        alone satisfies the assertion.
        """
        name_matches = [c for c in self if c.name.lower() == name.lower()]
        if not name_matches:
            fired = [c.name for c in self] or ["<none>"]
            raise AssertionError(
                f"expected tool {name!r} to have fired, but observed: {fired}"
            )
        if with_args is not None and not any(
            self._args_subset_matches(with_args, call.args) for call in name_matches
        ):
            observed = [c.args for c in name_matches]
            raise AssertionError(
                f"tool {name!r} fired, but none of its calls matched args "
                f"{with_args}; observed args: {observed}"
            )

    @staticmethod
    def _arg_matches(expected: Any, actual: Any) -> bool:
        """Tolerant single-arg match: substring for text, value-or-string else.

        Strings match case-insensitively as a substring (so a paraphrased value
        still matches); everything else matches by equality, with a string-coerced
        fallback so an int ``2`` matches a JSON-stringified ``"2"``.
        """
        if isinstance(expected, str) and isinstance(actual, str):
            return expected.lower() in actual.lower()
        if expected == actual:
            return True
        return str(expected) == str(actual)

    @staticmethod
    def _args_subset_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        """True if every expected key is present in ``actual`` and its value matches."""
        return all(
            key in actual and ToolCalls._arg_matches(value, actual[key])
            for key, value in expected.items()
        )
