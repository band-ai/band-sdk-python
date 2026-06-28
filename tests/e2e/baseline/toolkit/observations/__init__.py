"""Observation collections: what an agent emitted in a room, with assertions.

Each is a ``list`` subclass of captured items plus fluent, tolerant assertion
methods, so a check lives with the data it inspects. ``ReplyCapture`` (see
``capture.py``) surfaces both: ``messages`` (``Replies``) and ``tool_calls()``
(``ToolCalls``).

- :class:`Replies` — captured agent reply messages.
- :class:`ToolCalls` / :class:`ToolCall` — the agent's tool calls.
"""

from __future__ import annotations

from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall, ToolCalls

__all__ = ["Replies", "ToolCall", "ToolCalls"]
