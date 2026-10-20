"""Completion timers remain owned after terminal state and replacement."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from band.integrations.acp.server_adapter import BandACPServerAdapter
from band.integrations.acp.types import PendingACPPrompt
from tests.integrations.acp.serverkit import wait_for_pending_prompt
from tests.integrations.acp.serversettlement.kit import held_finalizer


class TestTimerSettlement:
    async def test_terminal_prompt_joins_removed_completion_timer(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)
        session = await adapter.create_session()
        room = adapter.get_room_for_session(session)
        assert room is not None
        async with held_finalizer() as timer:
            monkeypatch.setattr(adapter, "_complete_prompt_after_grace", timer.run)
            prompt = asyncio.create_task(adapter.handle_prompt(session, "Pending"))
            pending = await wait_for_pending_prompt(adapter, room)
            await adapter._schedule_prompt_completion(room, pending)
            await timer.entered.wait()
            await adapter.cancel_prompt(session)
            await timer.closing.wait()
            assert not prompt.done()
            timer.release.set()
            assert await prompt == "cancelled"
            assert timer.closed.is_set()

    async def test_replacement_joins_old_timer_outside_state_lock(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = BandACPServerAdapter(rest_client=mock_rest_client)
        session = await adapter.create_session()
        room = adapter.get_room_for_session(session)
        assert room is not None
        pending = PendingACPPrompt(session_id=session)
        adapter._pending_prompts[room] = pending
        async with held_finalizer() as timer:
            monkeypatch.setattr(adapter, "_complete_prompt_after_grace", timer.run)
            await adapter._schedule_prompt_completion(room, pending)
            await timer.entered.wait()
            replacement = asyncio.create_task(
                adapter._schedule_prompt_completion(room, pending)
            )
            await timer.closing.wait()
            assert not replacement.done()
            async with adapter._state_lock:
                timer.release.set()
            await replacement
            assert timer.closed.is_set()
            await adapter.cancel_prompt(session)
            assert pending.completion_task is not None
            await asyncio.gather(pending.completion_task, return_exceptions=True)
