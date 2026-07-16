"""Ergonomic test toolkit for the ACP client adapter.

The goal (mirroring the e2e baseline toolkit): tests read like intent, not
plumbing. Two primitives:

* :class:`FakeACPAgent` — a scripted, in-process ACP *agent*. Script it fluently
  (``.will_say(...)``, ``.will_call_tool(...)``, ``.will_ask_permission()``) or take
  full control with the ``@agent.on_prompt`` decorator. It speaks real ACP over the
  wire; only the "LLM" is canned.
* :func:`acp_adapter` — an async context manager that starts a real
  ``ACPClientAdapter`` wired to the agent over an **in-process socketpair** (genuine
  ACP JSON-RPC, no subprocess, no LLM) and yields an :class:`AcpSession` driver whose
  ``send()`` returns a readable :class:`Reply`.

Example::

    agent = FakeACPAgent().will_say("The weather is sunny.")
    async with acp_adapter(agent) as session:
        reply = await session.send("weather?", room="room-1")
    assert reply.texts == ["The weather is sunny."]
"""

from __future__ import annotations

from .agent import FakeACPAgent, PromptHandler
from .harness import (
    AcpSession,
    FakeSpawn,
    Reply,
    RoomActivity,
    TranscriptTools,
    acp_adapter,
    make_acp_connection,
)

__all__ = [
    "AcpSession",
    "FakeACPAgent",
    "FakeSpawn",
    "PromptHandler",
    "Reply",
    "RoomActivity",
    "TranscriptTools",
    "acp_adapter",
    "make_acp_connection",
]
