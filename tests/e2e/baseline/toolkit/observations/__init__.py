"""Observation collections: what an agent emitted in a room, with assertions.

Each is a ``list`` subclass of captured items plus fluent, tolerant assertion
methods, so a check lives with the data it inspects. ``ReplyCapture`` (see
``capture.py``) surfaces the room-scoped ones: ``messages`` (``Replies``),
``tool_calls()`` (``ToolCalls``), ``thoughts()``/``errors()``/``tasks()``
(``Events``), and ``memory(agent)`` (``MemoryObservation``).

- :class:`Replies` -- captured agent reply messages.
- :class:`ToolCall` / :class:`ToolCalls` -- the agent's tool calls (memory tools
  excluded by default); :class:`MemoryToolCalls` -- the call-layer memory view;
  :class:`MemoryTool` -- canonical memory tool names.
- :class:`Events` / :class:`Thoughts` / :class:`Errors` / :class:`Tasks` -- the
  free-text emitted events.
- :class:`Memories` -- the store-layer view: memory records that actually landed
  (agent-scoped). :class:`MemoryObservation` bundles both memory layers and is
  what ``ReplyCapture.memory`` returns.
- :class:`UsageRecord` / :class:`Usage` -- the agent's per-turn token usage
  (from ``usage`` events under ``Emit.USAGE``), what ``ReplyCapture.usage``
  returns.
"""

from __future__ import annotations

from tests.e2e.baseline.toolkit.observations.events import (
    Errors,
    Events,
    Tasks,
    Thoughts,
)
from tests.e2e.baseline.toolkit.observations.memories import (
    Memories,
    MemoryObservation,
)
from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.observations.tool_calls import (
    MemoryTool,
    MemoryToolCalls,
    ToolCall,
    ToolCalls,
)
from tests.e2e.baseline.toolkit.observations.usage import Usage, UsageRecord

__all__ = [
    "Errors",
    "Events",
    "Memories",
    "MemoryObservation",
    "MemoryTool",
    "MemoryToolCalls",
    "Replies",
    "Tasks",
    "Thoughts",
    "ToolCall",
    "ToolCalls",
    "Usage",
    "UsageRecord",
]
