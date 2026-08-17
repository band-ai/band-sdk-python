"""Tool-result observation capture and assertions for live E2E tests.

Captures the agent-under-test's tool_result events for a turn: which tool
produced them, and what it returned. Every adapter's tool_result content is
the canonical JSON shape ``ToolEventKey`` defines (``name``/``output``/
``tool_call_id``/``is_error``) -- decoded here via the shared
``band.converters.parsing.parse_tool_result``, the same tolerant decoder every
history converter uses, rather than any one adapter's own room-event model.

Tests reach this through ``ReplyCapture.tool_results`` (see ``capture.py``),
the tool_result analogue of ``ReplyCapture.tool_calls`` / :class:`ToolCalls`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from band_rest import ChatMessage

from band.converters.parsing import parse_tool_result
from band.core.types import MessageType

from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolResult:
    """One observed tool result: the tool name and the output it returned."""

    name: str
    output: str
    tool_call_id: str
    is_error: bool
    raw: ChatMessage

    @classmethod
    def from_event(cls, message: ChatMessage) -> ToolResult | None:
        """Build a ``ToolResult`` from a ``tool_result`` event's JSON content.

        Tolerant of shape drift: ``parse_tool_result`` returns ``None`` (logged,
        not raised) for a non-JSON or nameless payload, so a single odd event
        never breaks inspection.
        """
        parsed = parse_tool_result(message.content)
        if parsed is None:
            return None
        return cls(
            name=parsed.name,
            output=parsed.output,
            tool_call_id=parsed.tool_call_id,
            is_error=parsed.is_error,
            raw=message,
        )


class ToolResults(list[ToolResult]):
    """An agent's observed tool results for a turn: a ``list[ToolResult]`` with
    fluent, tolerant assertions.

    Being a list, it iterates, indexes, and ``len()``s like one. Read it once
    (see ``ToolResults.read`` / ``ReplyCapture.tool_results``), then assert as
    many times as needed against the same snapshot.
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
    ) -> ToolResults:
        """Read a room's tool results, oldest-first.

        Lists the room's ``tool_result`` events and parses each into a
        ``ToolResult``. Pass ``sender_id`` to keep only one agent's results
        (rooms can hold several agents). Call after the turn is known complete
        (e.g. after ``wait_for_processed``); tests usually reach this via
        ``ReplyCapture.tool_results``.
        """
        messages = await user_ops.list_messages(
            room_id, message_type=MessageType.TOOL_RESULT, since=since, limit=limit
        )
        results = cls()
        for message in messages:
            if sender_id is not None and message.sender_id != sender_id:
                continue
            result = ToolResult.from_event(message)
            if result is not None:
                results.append(result)
        return results

    def named(self, *names: str) -> ToolResults:
        """Return a same-class subset of the results matching any of ``names``
        (case-insensitive). The tool_result analogue of ``ToolCalls.named`` --
        use it to scope an assertion to one tool's results when a turn also
        narrates unrelated ones (e.g. an agent backend's internal tools)."""
        wanted = {name.lower() for name in names}
        return type(self)(result for result in self if result.name.lower() in wanted)

    def assert_present(self, *, what: str | None = None) -> None:
        """Assert at least one result was captured."""
        label = what or "a tool_result event"
        if not self:
            raise AssertionError(f"expected {label}, but none were emitted")

    def assert_json_output(self) -> None:
        """Assert every captured result's ``output`` is one well-formed JSON
        document.

        For a tool whose output is JSON (e.g. a Band platform tool's response),
        the emitted result must carry that payload exactly once -- a duplicated
        echo (the same payload concatenated twice, in any encoding) fails
        ``json.loads`` with "Extra data" and is reported with the offending
        content. Passes vacuously on an empty collection, so pair with
        ``assert_present``.
        """
        for result in self:
            try:
                json.loads(result.output)
            except json.JSONDecodeError as error:
                raise AssertionError(
                    f"expected tool_result output for {result.name!r} to be a "
                    f"single well-formed JSON document, but parsing failed "
                    f"({error}):\n{result.output}"
                ) from error
