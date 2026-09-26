"""Shared helper for awaiting a Codex adapter's detached turn.

Both ``tests/adapters/test_codex_adapter.py`` and
``tests/integrations/codex/test_adapter_e2e.py`` need the same thing: once a
manual approval releases the room, ``on_message``/``on_event`` returns early
and the turn keeps running in ``adapter._turn_tasks``, out of reach of the
caller that awaited the handler -- so a test must await it separately to
observe the turn's outcome.
"""

from __future__ import annotations

import asyncio

from band.adapters.codex import CodexAdapter


async def await_released_turn(
    adapter: CodexAdapter, room_id: str, *, timeout_s: float | None = None
) -> None:
    """Await ``room_id``'s detached turn, if one is running.

    A no-op when no turn is tracked (the room never released one). Pass
    ``timeout_s`` to bound the wait; omit it to await unconditionally.
    """
    turn = adapter._turn_tasks.get(room_id)
    if turn is None:
        return
    if timeout_s is None:
        await turn
    else:
        await asyncio.wait_for(turn, timeout=timeout_s)
