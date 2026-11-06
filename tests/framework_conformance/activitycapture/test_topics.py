"""Raw activity subscriptions await admission, retain payloads and own cleanup."""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.baseline.toolkit.activity import ActivityCapture, ActivityPush
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient
from tests.framework_conformance.activitycapture.topics import LEAVE_REJECTED, TopicPeer

DETAIL = {"kind": "tool_call", "name": "Read"}


class TestActivityTopics:
    async def test_failed_leave_does_not_abandon_other_topic_cleanup(
        self, raw_peer: tuple[TrackingWebSocketClient, TopicPeer]
    ) -> None:
        ws, peer = raw_peer
        peer.leave_failure = "room_activity:right"
        capture = ActivityCapture()
        with pytest.raises(ValueError, match=LEAVE_REJECTED):
            async with capture.rooms(ws, "left", "right"):
                pass
        assert peer.left == ["room_activity:left"]
        assert list(peer.callbacks) == ["room_activity:right"]
        peer.leave_failure = None
        await peer.leave_topic("room_activity:right")

    async def test_join_completes_before_any_trigger_is_admitted(
        self, raw_peer: tuple[TrackingWebSocketClient, TopicPeer]
    ) -> None:
        ws, peer = raw_peer
        capture = ActivityCapture()
        peer.release.clear()
        context = capture.rooms(ws, "left")
        joining = asyncio.create_task(context.__aenter__())
        try:
            await peer.entered.wait()
            assert not joining.done()
            peer.release.set()
            await joining
            await peer.emit(
                "left",
                ActivityPush.STARTED,
                {"agent_id": "current", "activity": DETAIL},
            )
            record = await capture.detail("left", "current", DETAIL)
            assert record.payload == {"agent_id": "current", "activity": DETAIL}
        finally:
            peer.release.set()
            await joining
            await context.__aexit__(None, None, None)
        assert peer.left == ["room_activity:left"]

    async def test_room_and_agent_filters_preserve_the_full_matching_payload(
        self, raw_peer: tuple[TrackingWebSocketClient, TopicPeer]
    ) -> None:
        ws, peer = raw_peer
        capture = ActivityCapture()
        async with capture.rooms(ws, "left", "right"):
            await peer.emit(
                "left", ActivityPush.CHANGED, {"agent_id": "other", "activity": DETAIL}
            )
            await peer.emit(
                "right",
                ActivityPush.CHANGED,
                {"agent_id": "current", "activity": DETAIL},
            )
            payload = {
                "agent_id": "current",
                "activity": DETAIL,
                "execution_id": "original",
                "extra": "retained",
            }
            await peer.emit("left", ActivityPush.CHANGED, payload)
            record = await capture.detail("left", "current", DETAIL)
            assert record.payload == payload
        assert peer.callbacks == {}
        assert peer.left == ["room_activity:right", "room_activity:left"]

    async def test_partial_join_failure_releases_already_joined_topics(
        self, raw_peer: tuple[TrackingWebSocketClient, TopicPeer]
    ) -> None:
        ws, peer = raw_peer
        peer.denied = "room_activity:right"
        capture = ActivityCapture()
        with pytest.raises(ValueError, match="Topic join rejected"):
            async with capture.rooms(ws, "left", "right"):
                pytest.fail("A rejected join cannot admit the scenario")
        assert peer.callbacks == {}
        assert peer.left == ["room_activity:left"]
