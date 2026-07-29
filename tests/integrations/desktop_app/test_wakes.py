"""The ledger deciding which mentions the view may wake Claude for.

The view sees a payload more than once — redelivery, a remount, its own
display loop — so the decision has to live server-side, made once per mention.
"""

from __future__ import annotations

from typing import Any

from tests.integrations.desktop_app.conftest import ids, mentioned_message, message


class TestWakeLedger:
    async def test_a_mention_the_join_turn_owns_is_never_woken_for(
        self, room: Any
    ) -> None:
        """Claude is already answering it; a wake would ask for it twice."""
        request = mentioned_message("req-1", "2026-01-01T00:00:01Z")
        live = room([request], [request])

        joined = await live.join()
        tick = await live.monitor()

        assert ids(joined["pending_requests"]) == ["req-1"]
        assert tick["wake_requests"] == []

    async def test_a_new_mention_is_handed_out_exactly_once(self, room: Any) -> None:
        request = mentioned_message("req-1", "2026-01-01T00:00:02Z")
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [request], [request])
        await live.join()

        first = await live.monitor()
        again = await live.monitor()

        assert ids(first["wake_requests"]) == ["req-1"]
        assert again["wake_requests"] == [], (
            "the mention is still pending, but it has already been offered"
        )

    async def test_a_claimed_wake_carries_the_words_to_deliver(self, room: Any) -> None:
        """The view relays this text; it must not have to compose any."""
        request = mentioned_message("req-1", "2026-01-01T00:00:02Z")
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [request], [request])
        await live.join()

        tick = await live.monitor()

        assert "Jerry" in tick["wake_prompt"]
        assert "connected Band agent" in tick["wake_prompt"]

    async def test_a_quiet_tick_carries_no_wake_text(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [])
        await live.join()

        assert (await live.monitor())["wake_prompt"] == ""

    async def test_a_wake_the_host_lost_is_offered_again(self, room: Any) -> None:
        """A dropped wake must not strand the mention unanswered forever."""
        request = mentioned_message("req-1", "2026-01-01T00:00:02Z")
        live = room([message("m-1", "2026-01-01T00:00:01Z")], [request], [request])
        await live.join()
        await live.monitor()

        retried = await live.monitor(retry_wakes=["req-1"])

        assert ids(retried["wake_requests"]) == ["req-1"]
