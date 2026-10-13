"""Agent runner rehydration."""

from __future__ import annotations

from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    build_runner,
    make_message_event,
    make_platform_message,
)


class TestAgentRunnerRehydration:
    async def test_forwards_one_nudge_per_room_with_backlog(self) -> None:
        runner, fwd, link = build_runner()
        link.get_next_message.side_effect = [
            make_platform_message("m-a", "r1"),
            make_platform_message("m-b", "r2"),
        ]

        await runner._rehydrate_backlog(["r1", "r2"])

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-a", "m-b"}
        # Each forwarded nudge looks like a live message_created event.
        assert all(p["event_type"] == "message_created" for p in fwd.forwarded)

    async def test_skips_rooms_with_no_backlog(self) -> None:
        runner, fwd, link = build_runner()
        link.get_next_message.return_value = None

        await runner._rehydrate_backlog(["r1", "r2"])

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []

    async def test_next_failure_does_not_block_other_rooms(self) -> None:
        runner, fwd, link = build_runner()
        link.get_next_message.side_effect = [
            RuntimeError("boom"),
            make_platform_message("m-b", "r2"),
        ]

        await runner._rehydrate_backlog(["r1", "r2"])

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-b"}

    async def test_rehydrated_message_dedups_against_live_event(self) -> None:
        runner, fwd, link = build_runner()
        link.get_next_message.side_effect = [make_platform_message("m-dup", "r1")]

        await runner._rehydrate_backlog(["r1"])
        # Same message id then arrives live on the WS.
        await runner._handle_event(make_message_event(message_id="m-dup", room_id="r1"))

        assert isinstance(fwd, FakeForwarder)
        assert len(fwd.forwarded) == 1

    async def test_failed_forward_is_retryable_on_rehydration(self) -> None:
        """Regression: dedup must not mask a message whose first forward failed.

        If the bridge marks a message id as processed before the forward
        succeeds, a transient forwarder failure permanently swallows that
        message — on reconnect the rehydration sweep sees it again and drops
        it as a duplicate, so the room is stuck.
        """
        fwd = FakeForwarder()
        runner, _, link = build_runner(forwarder=fwd)

        # First forward raises; second forward (after rehydration) succeeds.
        fwd.forward_side_effect = RuntimeError("network down")
        await runner._safe_handle_event(
            make_message_event(message_id="m-stuck", room_id="r1")
        )
        assert fwd.forwarded == []  # forward failed → nothing recorded

        # Rehydration replays the same message id; bridge must attempt the
        # forward again rather than treat it as already-processed.
        fwd.forward_side_effect = None
        link.get_next_message.side_effect = [make_platform_message("m-stuck", "r1")]
        await runner._rehydrate_backlog(["r1"])

        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-stuck"}
