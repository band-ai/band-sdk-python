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

from tests.integrations.acp.acp_toolkit.agent import (
    FakeACPAgent,
    PromptHandler,
    select_option,
)
from tests.integrations.acp.acp_toolkit.harness import (
    DEFAULT_ROOM,
    AcpSession,
    FakeSpawn,
    Launch,
    Reply,
    RoomActivity,
    TranscriptTools,
    acp_adapter,
    fake_agent_config,
    inject_acp_spawn,
    launch_for,
    live_line,
    make_acp_connection,
    started_acp_adapter,
)

__all__ = [
    "DEFAULT_ROOM",
    "AcpSession",
    "FakeACPAgent",
    "FakeSpawn",
    "Launch",
    "PromptHandler",
    "Reply",
    "RoomActivity",
    "TranscriptTools",
    "acp_adapter",
    "fake_agent_config",
    "inject_acp_spawn",
    "launch_for",
    "live_line",
    "make_acp_connection",
    "select_option",
    "started_acp_adapter",
]
