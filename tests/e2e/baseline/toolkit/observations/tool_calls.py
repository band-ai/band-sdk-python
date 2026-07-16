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
already persisted and queryable. A real-time path would instead need to buffer
the chat-room WebSocket notifications for non-text rows; reading the persisted
events is simpler and sufficient for asserting what fired.

Tests reach this through ``ReplyCapture.tool_calls`` (see ``capture.py``), which
returns a :class:`ToolCalls` carrying the calls plus a fluent ``assert_fired``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from band_rest import ChatMessage

from band.core.types import MessageType
from band.runtime.tools import MEMORY_TOOL_NAMES

from tests.e2e.baseline.toolkit.observations.matching import tolerant_match
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


class MemoryTool(StrEnum):
    """Canonical memory platform-tool names as named members (over raw strings).

    Values are validated against the SDK's ``MEMORY_TOOL_NAMES`` at import (below),
    so they cannot drift from that source of truth -- if a tool is renamed there,
    importing this module fails loudly.
    """

    STORE = "band_store_memory"
    LIST = "band_list_memories"
    GET = "band_get_memory"
    SUPERSEDE = "band_supersede_memory"
    ARCHIVE = "band_archive_memory"


if {tool.value for tool in MemoryTool} != set(MEMORY_TOOL_NAMES):
    raise ValueError(
        "MemoryTool drifted from band.runtime.tools.MEMORY_TOOL_NAMES: "
        f"{set(MEMORY_TOOL_NAMES) ^ {tool.value for tool in MemoryTool}}"
    )


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
        # Args may arrive as a dict or, for some adapters (pydantic-ai), as a JSON
        # string — normalize both to a dict so assertions are adapter-agnostic.
        args = payload.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
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

    The general view **opts memory tools out by default** (``include_memory=False``)
    so generic tool assertions aren't polluted by memory operations -- mirroring
    the SDK's own ``BASE_TOOL_NAMES = ALL_TOOL_NAMES - MEMORY_TOOL_NAMES`` split.
    The ``TOOL_NAMES`` hook lets a subclass restrict the view to a fixed set (see
    :class:`MemoryToolCalls`); ``None`` means "base, memory excluded".
    """

    TOOL_NAMES: ClassVar[frozenset[str] | None] = None

    @classmethod
    async def read(
        cls,
        user_ops: UserOps,
        room_id: str,
        *,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        include_memory: bool = False,
    ) -> ToolCalls:
        """Read a room's tool calls, oldest-first.

        Lists the room's ``tool_call`` events and parses each into a ``ToolCall``.
        Pass ``sender_id`` to keep only one agent's calls (rooms can hold several
        agents). Call after the turn is known complete (e.g. after
        ``wait_for_processed``); tests usually reach this via
        ``ReplyCapture.tool_calls``.

        When ``TOOL_NAMES`` is set (a subclass), only those tools are kept. On the
        base view, memory tools are excluded unless ``include_memory=True``.

        Without ``since`` this returns every matching tool call in the room (a
        lower bound on a single turn). Pass ``since`` (a server timestamp) to
        exclude earlier turns when reusing a capture across turns.
        """
        messages = await user_ops.list_messages(
            room_id, message_type=MessageType.TOOL_CALL, since=since, limit=limit
        )
        calls = cls()
        for message in messages:
            if sender_id is not None and message.sender_id != sender_id:
                continue
            call = ToolCall.from_event(message)
            if call is not None and cls._in_view(call.name, include_memory):
                calls.append(call)
        return calls

    @classmethod
    def _in_view(cls, name: str, include_memory: bool) -> bool:
        """Whether a tool ``name`` belongs to this view (see class docstring)."""
        if cls.TOOL_NAMES is not None:
            # A bound view (e.g. MemoryToolCalls) is defined entirely by its
            # TOOL_NAMES; the base-only include_memory flag does not apply to it.
            return name in cls.TOOL_NAMES
        return include_memory or name not in MEMORY_TOOL_NAMES

    def named(self, *names: str) -> ToolCalls:
        """Return a same-class subset of the calls matching any of ``names``
        (case-insensitive). Re-wrapped so the assertions stay available."""
        wanted = {name.lower() for name in names}
        return type(self)(call for call in self if call.name.lower() in wanted)

    def fired(self, name: str) -> bool:
        """True if any call matches ``name`` (case-insensitive)."""
        return any(call.name.lower() == name.lower() for call in self)

    def assert_fired(
        self, name: str, *, with_args: dict[str, Any] | None = None
    ) -> None:
        """Assert a tool named ``name`` fired, optionally with the expected args.

        Tolerant by design: the name matches case-insensitively, and ``with_args``
        is a *subset* check (the call may carry extra args), each value matched via
        ``tolerant_match``. It is not an exact-args assertion; agents vary phrasing
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
    def _args_subset_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        """True if every expected key is present in ``actual`` and its value
        ``tolerant_match``es."""
        return all(
            key in actual and tolerant_match(value, actual[key])
            for key, value in expected.items()
        )


class MemoryToolCalls(ToolCalls):
    """The call-layer memory view: an agent's memory tool calls for a turn.

    Restricts the view to ``MEMORY_TOOL_NAMES`` and adds operation-named
    assertions that read clearer than ``assert_fired("band_store_memory", ...)``.
    This is the *call* layer (the agent invoked a memory tool); the *store*
    layer (a memory record actually exists) is ``observations.memories.Memories``.
    Hence ``assert_store_called`` here vs ``assert_stored`` there.
    """

    TOOL_NAMES: ClassVar[frozenset[str] | None] = MEMORY_TOOL_NAMES

    def assert_store_called(
        self,
        *,
        content: str | None = None,
        scope: Any | None = None,
        system: Any | None = None,
        type: Any | None = None,
        segment: Any | None = None,
        subject_id: str | None = None,
    ) -> None:
        """Assert ``band_store_memory`` fired, optionally with the given params
        (a tolerant subset match over the call's args, like ``assert_fired``)."""
        with_args = {
            key: value
            for key, value in {
                "content": content,
                "scope": scope,
                "system": system,
                "type": type,
                "segment": segment,
                "subject_id": subject_id,
            }.items()
            if value is not None
        }
        self.assert_fired(MemoryTool.STORE, with_args=with_args or None)

    def assert_list_called(self) -> None:
        """Assert ``band_list_memories`` fired."""
        self.assert_fired(MemoryTool.LIST)

    def assert_get_called(self) -> None:
        """Assert ``band_get_memory`` fired."""
        self.assert_fired(MemoryTool.GET)

    def assert_supersede_called(self) -> None:
        """Assert ``band_supersede_memory`` fired."""
        self.assert_fired(MemoryTool.SUPERSEDE)

    def assert_archive_called(self) -> None:
        """Assert ``band_archive_memory`` fired."""
        self.assert_fired(MemoryTool.ARCHIVE)
