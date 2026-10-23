from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from bandpap.room import RoomDelivery, RoomOutbox
from bandpap.storage import StateConflict, StateStore

from tests.pap.adapterkit import harness


class TestRoomOutbox:
    @pytest.mark.asyncio
    async def test_uncertain_band_send_is_not_automatically_repeated_after_restart(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        delivery = RoomDelivery(
            id="delivery",
            room="room",
            proxy="target",
            recipients=("muse",),
            grant="grant",
            text="hello",
        )
        await h.outbox.accept(delivery)
        h.sender.error = TimeoutError()
        await h.outbox.flush(proxy="target")
        restarted = RoomOutbox(
            StateStore(tmp_path / "state"),
            sender=h.sender,
            authorize=h.outbox.authorize,
        )
        await restarted.accept(delivery)
        await restarted.flush(proxy="target")
        assert len(h.sender.sent) == 1
        assert restarted.state("delivery") == ("uncertain", None)
        with pytest.raises(StateConflict):
            await restarted.reconcile("delivery", remote_id="")
        with pytest.raises(StateConflict):
            await restarted.reconcile("delivery", remote_id=None)
        await restarted.reconcile("delivery", remote_id="confirmed_remote")
        assert restarted.state("delivery") == ("delivered", "confirmed_remote")

    @pytest.mark.asyncio
    async def test_id_collision_cannot_change_recipient_or_content(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        d = RoomDelivery(
            id="delivery",
            room="room",
            proxy="target",
            recipients=("muse",),
            grant="grant",
            text="hello",
        )
        await h.outbox.accept(d)
        with pytest.raises(StateConflict):
            await h.outbox.accept(d.model_copy(update={"text": "changed"}))
        assert h.outbox.health(proxy="target") == {
            "ready": 1,
            "uncertain": 0,
            "delivered": 0,
        }

    @pytest.mark.asyncio
    async def test_denied_grants_cannot_exhaust_budget_before_valid_owner(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        original = h.outbox.authorize
        h.outbox.authorize = AsyncMock()
        for index in range(3):
            await h.outbox.accept(
                RoomDelivery(
                    id=f"denied-{index}",
                    room="room",
                    proxy="target",
                    recipients=("muse",),
                    grant=f"denied-{index}",
                    text="private",
                )
            )
        await h.outbox.accept(
            RoomDelivery(
                id="allowed",
                room="room",
                proxy="target",
                recipients=("muse",),
                grant="grant",
                text="allowed",
            )
        )
        h.outbox.authorize = original
        assert await h.outbox.flush(proxy="target", limit=1) == 1
        assert [delivery.id for delivery in h.sender.sent] == ["allowed"]
