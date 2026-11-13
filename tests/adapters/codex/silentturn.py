"""Silence limits for Codex turns on the virtual clock: their configs, what a
busy server sends, and the failure silence reports."""

from __future__ import annotations

from typing import Any

from band.adapters.codex import CodexAdapterConfig
from band.core.protocols import FAILURE_CODE_TIMEOUT
from band.integrations.codex import RpcEvent
from tests.adapters.codexturns import FIRST_THREAD_ID, event_notification

# The silence a turn is allowed, and a total limit no test's turn reaches.
IDLE_S = 120.0
CEILING_S = 3600.0
# How often a busy turn's server is heard from.
HEARD_EVERY_S = 60.0


def silence_guarded(**limits: float) -> CodexAdapterConfig:
    """Stop a turn after ``IDLE_S`` of silence, unless ``limits`` say otherwise."""
    settings: dict[str, Any] = {
        "turn_idle_timeout_s": IDLE_S,
        "turn_timeout_s": CEILING_S,
        **limits,
    }
    return CodexAdapterConfig(**settings)


def thinking() -> RpcEvent:
    """A reasoning delta, which the adapter reads without telling the room."""
    return _reasoning_delta({"threadId": FIRST_THREAD_ID})


def sub_agent_thinking(thread_id: str) -> RpcEvent:
    """The same delta from a sub-agent, under its own thread and turn."""
    return _reasoning_delta({"threadId": thread_id, "turnId": f"{thread_id}-turn"})


def _reasoning_delta(named: dict[str, str]) -> RpcEvent:
    return event_notification(
        "item/reasoning/summaryTextDelta",
        {**named, "itemId": "reasoning-1", "delta": "."},
    )


def silent_for(
    seconds: float, limit: str = "turn_idle_timeout_s"
) -> list[tuple[str, str]]:
    """The one failure of a turn stopped after ``seconds`` without an event."""
    return [
        (
            FAILURE_CODE_TIMEOUT,
            f"Codex turn timed out after {seconds}s of silence ({limit})",
        )
    ]
