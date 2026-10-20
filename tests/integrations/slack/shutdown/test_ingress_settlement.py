"""Detached, acknowledged webhook work is part of global teardown."""

from __future__ import annotations

import asyncio

import pytest

from tests.integrations.slack.shutdown.kit import GatedBrain
from tests.integrations.slack.wrappingkit import (
    _make_adapter,
    _mention_event,
    _post_slack_event,
)


class TestIngressSettlement:
    async def test_acknowledged_work_finishes_before_inner_cleanup(self) -> None:
        brain = GatedBrain()
        adapter, _, _, _ = _make_adapter(inner=brain)
        await adapter.on_started("Bridge", "")
        response = await _post_slack_event(
            adapter, adapter.config.apps[0], _mention_event()
        )
        assert response.status_code == 200
        await brain.entered.wait()
        stopping = asyncio.create_task(adapter.cleanup_all())
        try:
            await brain.finalizing.wait()
            assert not stopping.done()
            assert brain.cleanup_calls == 0
            brain.release.set()
            await stopping
            assert brain.finished.is_set()
            assert brain.cleanup_calls == 1
            brain.entered.clear()
            response = await _post_slack_event(
                adapter, adapter.config.apps[0], _mention_event(ts="2")
            )
            assert response.status_code == 200
            await adapter.wait_idle()
            assert not brain.entered.is_set()
        finally:
            brain.release.set()
            await stopping

    @pytest.mark.parametrize("started", [False, True])
    async def test_only_attempted_inner_startup_is_unwound(self, started: bool) -> None:
        brain = GatedBrain()
        adapter, _, _, _ = _make_adapter(inner=brain)
        if started:
            brain.fail_start = True
            with pytest.raises(OSError, match="Inner startup"):
                await adapter.on_started("Bridge", "")
        await adapter.cleanup_all()
        await adapter.cleanup_all()
        assert brain.cleanup_calls == int(started)

    async def test_callback_free_restart_dispatches_again(self) -> None:
        adapter, brain, _, _ = _make_adapter(room_ids=["room-1", "room-2"])
        for run in range(2):
            await adapter.on_started("Bridge", "")
            response = await _post_slack_event(
                adapter, adapter.config.apps[0], _mention_event(ts=str(run))
            )
            assert response.status_code == 200
            await adapter.wait_idle()
            assert len(brain.invocations) == run + 1
            await adapter.cleanup_all()
