"""BandLinkEventQueue behavior."""

from __future__ import annotations

from band.platform.link import BandLink
from tests.conftest import make_message_event


class TestBandLinkEventQueue:
    """Test event queue mechanism (async iterator pattern)."""

    def test_queue_event_adds_to_queue(self):
        """_queue_event() should add event to queue."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        link._queue_event(event)

        assert link._event_queue.qsize() == 1

    async def test_async_iteration_gets_events(self):
        """async for should yield events from queue."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        link._queue_event(event)

        # Get event via async iteration
        received = await link.__anext__()
        assert received is event

    def test_queue_drops_when_full(self):
        """Queue should drop events when full (no blocking)."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        # Fill the queue (maxsize=1000)
        for i in range(1000):
            link._queue_event(make_message_event(msg_id=f"msg-{i}"))

        # Queue should be full
        assert link._event_queue.full()
