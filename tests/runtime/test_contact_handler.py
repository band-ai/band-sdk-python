"""Tests for ContactEventHandler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.event import (
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
)
from band.client.streaming import (
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)
from band.runtime.contact_handler import ContactEventHandler, MAX_DEDUP_CACHE_SIZE
from band.runtime.contact_tools import ContactTools
from band.runtime.types import ContactEventConfig, ContactEventStrategy


@pytest.fixture
def mock_link():
    """Mock BandLink for testing."""
    link = MagicMock()
    link.rest = MagicMock()
    return link


@pytest.fixture
def mock_callback():
    """Mock callback for CALLBACK strategy."""
    return AsyncMock()


@pytest.fixture
def sample_request_received_event():
    """Sample ContactRequestReceivedEvent."""
    return ContactRequestReceivedEvent(
        payload=ContactRequestReceivedPayload(
            id="req-123",
            from_handle="@alice",
            from_name="Alice",
            message="Hello!",
            status="pending",
            inserted_at="2024-01-01T00:00:00Z",
        )
    )


@pytest.fixture
def sample_contact_added_event():
    """Sample ContactAddedEvent."""
    return ContactAddedEvent(
        payload=ContactAddedPayload(
            id="contact-123",
            handle="@bob",
            name="Bob",
            type="User",
            inserted_at="2024-01-01T00:00:00Z",
        )
    )


@pytest.fixture
def sample_contact_removed_event():
    """Sample ContactRemovedEvent."""
    return ContactRemovedEvent(payload=ContactRemovedPayload(id="contact-456"))


class TestDisabledStrategy:
    """Tests for DISABLED strategy."""

    async def test_disabled_strategy_ignores_events(
        self, mock_link, sample_request_received_event
    ):
        """DISABLED strategy should do nothing."""
        config = ContactEventConfig(strategy=ContactEventStrategy.DISABLED)
        handler = ContactEventHandler(config, mock_link)

        await handler.handle(sample_request_received_event)

        # No callback should be called, no error should occur


class TestCallbackStrategy:
    """Tests for CALLBACK strategy."""

    async def test_callback_strategy_calls_on_event(
        self, mock_link, mock_callback, sample_request_received_event
    ):
        """CALLBACK strategy should call provided callback."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=mock_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        await handler.handle(sample_request_received_event)

        mock_callback.assert_called_once()

    async def test_callback_receives_contact_tools(
        self, mock_link, mock_callback, sample_request_received_event
    ):
        """Callback should receive ContactTools instance."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=mock_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        await handler.handle(sample_request_received_event)

        call_args = mock_callback.call_args
        assert isinstance(call_args[0][1], ContactTools)

    async def test_callback_receives_correct_event(
        self, mock_link, mock_callback, sample_request_received_event
    ):
        """Event passed to callback should match input event."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=mock_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        await handler.handle(sample_request_received_event)

        call_args = mock_callback.call_args
        assert call_args[0][0] is sample_request_received_event

    async def test_callback_exception_logged_not_raised(
        self, mock_link, sample_request_received_event
    ):
        """Callback errors should be logged but not crash handler."""

        async def failing_callback(event, tools):
            raise ValueError("Callback failed!")

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=failing_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        # Should not raise
        await handler.handle(sample_request_received_event)


class TestDeduplication:
    """Tests for event deduplication."""

    async def test_dedup_skips_duplicate_request(
        self, mock_link, mock_callback, sample_request_received_event
    ):
        """Same event should only be processed once."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=mock_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        # Process same event twice
        await handler.handle(sample_request_received_event)
        await handler.handle(sample_request_received_event)

        # Callback should only be called once
        assert mock_callback.call_count == 1

    async def test_dedup_allows_different_requests(self, mock_link, mock_callback):
        """Different events should both be processed."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=mock_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        event1 = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-111",
                from_handle="@alice",
                from_name="Alice",
                message=None,
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )
        event2 = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-222",
                from_handle="@bob",
                from_name="Bob",
                message=None,
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )

        await handler.handle(event1)
        await handler.handle(event2)

        assert mock_callback.call_count == 2

    async def test_dedup_cache_bounded(self, mock_link, mock_callback):
        """Cache should not grow unbounded."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=mock_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        # Process more events than cache limit
        for i in range(MAX_DEDUP_CACHE_SIZE + 100):
            event = ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id=f"req-{i}",
                    from_handle=f"@user{i}",
                    from_name=f"User {i}",
                    message=None,
                    status="pending",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            )
            await handler.handle(event)

        # Cache should be bounded
        assert len(handler._processed_events) <= MAX_DEDUP_CACHE_SIZE

    async def test_dedup_cleared_on_failure(
        self, mock_link, sample_request_received_event
    ):
        """Failed events should be cleared from dedup cache for retry."""
        call_count = 0

        async def failing_then_succeeding(event, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("First attempt fails")
            # Second attempt succeeds

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=failing_then_succeeding,
        )
        handler = ContactEventHandler(config, mock_link)

        # First attempt (fails)
        await handler.handle(sample_request_received_event)
        # Second attempt (should not be skipped due to dedup)
        await handler.handle(sample_request_received_event)

        # Callback should be called twice
        assert call_count == 2


class TestDedupKeyGeneration:
    """Tests for deduplication key generation."""

    def test_get_dedup_key_request_received(self, mock_link):
        """ContactRequestReceivedEvent key format."""
        config = ContactEventConfig()
        handler = ContactEventHandler(config, mock_link)

        event = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-123",
                from_handle="@alice",
                from_name="Alice",
                message=None,
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )

        key = handler._get_dedup_key(event)
        assert key == "request_received:req-123"

    def test_get_dedup_key_request_updated(self, mock_link):
        """ContactRequestUpdatedEvent key includes status."""
        config = ContactEventConfig()
        handler = ContactEventHandler(config, mock_link)

        event = ContactRequestUpdatedEvent(
            payload=ContactRequestUpdatedPayload(
                id="req-456",
                status="approved",
            )
        )

        key = handler._get_dedup_key(event)
        assert key == "request_updated:req-456:approved"

    def test_get_dedup_key_contact_added(self, mock_link, sample_contact_added_event):
        """ContactAddedEvent key format."""
        config = ContactEventConfig()
        handler = ContactEventHandler(config, mock_link)

        key = handler._get_dedup_key(sample_contact_added_event)
        assert key == "contact_added:contact-123"

    def test_get_dedup_key_contact_removed(
        self, mock_link, sample_contact_removed_event
    ):
        """ContactRemovedEvent key format."""
        config = ContactEventConfig()
        handler = ContactEventHandler(config, mock_link)

        key = handler._get_dedup_key(sample_contact_removed_event)
        assert key == "contact_removed:contact-456"


class TestBroadcastChanges:
    """Tests for broadcast_changes functionality."""

    async def test_broadcast_contact_added(self, mock_link, sample_contact_added_event):
        """ContactAddedEvent should trigger broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_added_event)

        assert len(broadcast_messages) == 1
        assert "@bob (Bob) is now a contact" in broadcast_messages[0]

    async def test_broadcast_contact_removed(
        self, mock_link, sample_contact_removed_event
    ):
        """ContactRemovedEvent should trigger broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_removed_event)

        assert len(broadcast_messages) == 1
        assert "contact-456 was removed" in broadcast_messages[0]

    async def test_broadcast_not_triggered_for_request_events(
        self, mock_link, sample_request_received_event
    ):
        """Request events should not trigger broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_request_received_event)

        assert len(broadcast_messages) == 0

    async def test_broadcast_disabled_by_default(
        self, mock_link, sample_contact_added_event
    ):
        """broadcast_changes=False should not trigger broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=False,  # Default
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_added_event)

        assert len(broadcast_messages) == 0


class TestHandlerStats:
    """Tests for handler statistics."""

    def test_get_stats_returns_expected_fields(self, mock_link):
        """get_stats() should return expected fields."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link)

        stats = handler.get_stats()

        assert stats["strategy"] == "disabled"
        assert stats["dedup_cache_size"] == 0
        assert stats["hub_room_id"] is None
        assert stats["broadcast_enabled"] is True


class TestHubRoomStrategy:
    """Tests for HUB_ROOM strategy.

    The HUB_ROOM strategy injects MessageEvents into a dedicated hub room's
    ExecutionContext for LLM processing (not REST API calls).
    """

    @pytest.fixture
    def mock_hub_link(self):
        """Mock BandLink for HUB_ROOM testing."""
        link = MagicMock()
        link.rest = MagicMock()

        # Mock create_agent_chat (for room creation at startup)
        mock_chat_response = MagicMock()
        mock_chat_response.data = MagicMock()
        mock_chat_response.data.id = "hub-room-123"
        link.rest.agent_api_chats = MagicMock()
        link.rest.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_chat_response
        )

        # Mock create_agent_chat_event (for task event posting)
        mock_event_response = MagicMock()
        mock_event_response.data = MagicMock()
        mock_event_response.data.id = "event-123"
        link.rest.agent_api_events = MagicMock()
        link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
            return_value=mock_event_response
        )

        return link

    @pytest.fixture
    def mock_hub_event_callback(self):
        """Mock callback for hub event injection."""
        return AsyncMock()

    async def test_hub_room_creates_room_at_startup(
        self, mock_hub_link, mock_hub_event_callback, sample_request_received_event
    ):
        """Hub room should be created at startup via initialize_hub_room()."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initially no hub room
        assert handler._hub_room_id is None

        # Initialize hub room at startup
        await handler.initialize_hub_room()

        # Now hub room should be created
        assert handler._hub_room_id == "hub-room-123"
        mock_hub_link.rest.agent_api_chats.create_agent_chat.assert_called_once()

    async def test_hub_room_reuses_existing_room(
        self, mock_hub_link, mock_hub_event_callback, sample_request_received_event
    ):
        """Same hub room should be reused for all events."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize hub room at startup and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        # Handle two events
        await handler.handle(sample_request_received_event)

        # Reset dedup cache to allow second event
        handler._processed_events.clear()
        await handler.handle(sample_request_received_event)

        # create_agent_chat should only be called once (at startup)
        assert mock_hub_link.rest.agent_api_chats.create_agent_chat.call_count == 1

        # on_hub_event should be called twice
        assert mock_hub_event_callback.call_count == 2

    async def test_hub_room_thread_safe(self, mock_hub_link, mock_hub_event_callback):
        """Concurrent events should reuse the same hub room."""
        import asyncio

        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize hub room at startup and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        # Create multiple distinct events
        events = [
            ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id=f"req-{i}",
                    from_handle=f"@user{i}",
                    from_name=f"User {i}",
                    message=None,
                    status="pending",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            )
            for i in range(5)
        ]

        # Handle all events concurrently
        await asyncio.gather(*[handler.handle(e) for e in events])

        # Only one room should be created (at startup)
        assert mock_hub_link.rest.agent_api_chats.create_agent_chat.call_count == 1

    async def test_hub_room_uses_custom_task_id(
        self, mock_hub_link, mock_hub_event_callback, sample_request_received_event
    ):
        """Custom hub_task_id should be used when creating room."""
        task_uuid = "550e8400-e29b-41d4-a716-446655440000"
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            hub_task_id=task_uuid,
        )
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize hub room at startup
        await handler.initialize_hub_room()

        call_args = mock_hub_link.rest.agent_api_chats.create_agent_chat.call_args
        chat_request = call_args.kwargs.get("chat") or call_args[1].get("chat")
        assert chat_request.task_id == task_uuid

    async def test_hub_room_injects_message_event(
        self, mock_hub_link, mock_hub_event_callback, sample_request_received_event
    ):
        """Events should be injected as MessageEvent with type 'text'."""
        from band.platform.event import MessageEvent

        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        await handler.handle(sample_request_received_event)

        # Verify callback was called
        mock_hub_event_callback.assert_called_once()

        # Get the injected MessageEvent
        call_args = mock_hub_event_callback.call_args
        hub_room_id = call_args[0][0]
        message_event = call_args[0][1]

        assert hub_room_id == "hub-room-123"
        assert isinstance(message_event, MessageEvent)
        assert message_event.payload.message_type == "text"
        assert message_event.payload.sender_type == "System"
        assert message_event.payload.sender_name == "Contact Events"

    async def test_hub_room_event_contains_metadata(
        self, mock_hub_link, mock_hub_event_callback, sample_request_received_event
    ):
        """Event raw data should include contact_event_type."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        await handler.handle(sample_request_received_event)

        call_args = mock_hub_event_callback.call_args
        message_event = call_args[0][1]

        assert message_event.raw["contact_event_type"] == "contact_request_received"

    async def test_hub_room_formats_request_received(
        self, mock_hub_link, mock_hub_event_callback
    ):
        """ContactRequestReceivedEvent should be formatted correctly."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        event = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-format-test",
                from_handle="@alice",
                from_name="Alice Smith",
                message="Hi, let's connect!",
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )

        await handler.handle(event)

        call_args = mock_hub_event_callback.call_args
        message_event = call_args[0][1]
        content = message_event.payload.content

        assert "[Contact Request]" in content
        assert "Alice Smith" in content
        assert "@alice" in content
        assert "wants to connect" in content
        assert "Hi, let's connect!" in content
        assert "req-format-test" in content

    async def test_hub_room_formats_request_updated(
        self, mock_hub_link, mock_hub_event_callback
    ):
        """ContactRequestUpdatedEvent should be formatted correctly."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        event = ContactRequestUpdatedEvent(
            payload=ContactRequestUpdatedPayload(
                id="req-update-test",
                status="approved",
            )
        )

        await handler.handle(event)

        call_args = mock_hub_event_callback.call_args
        message_event = call_args[0][1]
        content = message_event.payload.content

        assert "[Contact Request Update]" in content
        assert "req-update-test" in content
        assert "approved" in content

    async def test_hub_room_formats_contact_added(
        self, mock_hub_link, mock_hub_event_callback, sample_contact_added_event
    ):
        """ContactAddedEvent should be formatted correctly."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        await handler.handle(sample_contact_added_event)

        call_args = mock_hub_event_callback.call_args
        message_event = call_args[0][1]
        content = message_event.payload.content

        assert "[Contact Added]" in content
        assert "Bob" in content
        assert "@bob" in content
        assert "is now a contact" in content

    async def test_hub_room_formats_contact_removed(
        self, mock_hub_link, mock_hub_event_callback, sample_contact_removed_event
    ):
        """ContactRemovedEvent should be formatted correctly."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        await handler.handle(sample_contact_removed_event)

        call_args = mock_hub_event_callback.call_args
        message_event = call_args[0][1]
        content = message_event.payload.content

        assert "[Contact Removed]" in content
        assert "contact-456" in content

    async def test_hub_room_error_returns_false(
        self, mock_hub_link, sample_request_received_event
    ):
        """Errors during hub room handling should return False."""
        # Create callback that raises
        failing_callback = AsyncMock(side_effect=Exception("Injection error"))

        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=failing_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        # Should not raise
        await handler.handle(sample_request_received_event)

        # Event should not be marked as processed (can be retried)
        key = handler._get_dedup_key(sample_request_received_event)
        assert key not in handler._processed_events

    async def test_hub_room_fails_without_callback(
        self, mock_hub_link, sample_request_received_event
    ):
        """HUB_ROOM without on_hub_event callback should fail gracefully."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(config, mock_hub_link)  # No callback

        # Initialize hub room (but no callback to inject events)
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        # Should not raise
        await handler.handle(sample_request_received_event)

        # Event should not be marked as processed
        key = handler._get_dedup_key(sample_request_received_event)
        assert key not in handler._processed_events

    async def test_hub_room_injects_system_prompt_on_first_event(
        self, mock_hub_link, mock_hub_event_callback, sample_request_received_event
    ):
        """System prompt should be injected on first event only."""
        from band.runtime.contact_handler import HUB_ROOM_SYSTEM_PROMPT

        mock_hub_init_callback = AsyncMock()

        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config,
            mock_hub_link,
            on_hub_event=mock_hub_event_callback,
            on_hub_init=mock_hub_init_callback,
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        # Handle first event
        await handler.handle(sample_request_received_event)

        # System prompt should be injected
        mock_hub_init_callback.assert_called_once()
        call_args = mock_hub_init_callback.call_args
        assert call_args[0][0] == "hub-room-123"  # Room ID
        assert call_args[0][1] == HUB_ROOM_SYSTEM_PROMPT

        # Handle second event (clear dedup cache first)
        handler._processed_events.clear()
        await handler.handle(sample_request_received_event)

        # System prompt should NOT be injected again
        assert mock_hub_init_callback.call_count == 1

    async def test_hub_room_system_prompt_contains_instructions(self):
        """System prompt should contain contact management instructions."""
        from band.runtime.contact_handler import HUB_ROOM_SYSTEM_PROMPT

        # Verify key instructions are present
        assert "contact requests" in HUB_ROOM_SYSTEM_PROMPT.lower()
        assert "band_respond_contact_request" in HUB_ROOM_SYSTEM_PROMPT
        assert "approve" in HUB_ROOM_SYSTEM_PROMPT.lower()
        assert "reject" in HUB_ROOM_SYSTEM_PROMPT.lower()
        assert "thought" in HUB_ROOM_SYSTEM_PROMPT.lower()

        # Verify override instructions to prevent delegation
        assert "do not delegate" in HUB_ROOM_SYSTEM_PROMPT.lower()
        assert "do not call band_lookup_peers" in HUB_ROOM_SYSTEM_PROMPT.lower()
        assert (
            "do not" in HUB_ROOM_SYSTEM_PROMPT.lower()
            and "add_participant" in HUB_ROOM_SYSTEM_PROMPT.lower()
        )

    async def test_hub_room_request_update_includes_cached_sender_info(
        self, mock_hub_link, mock_hub_event_callback
    ):
        """ContactRequestUpdatedEvent should include sender info from cached request."""
        config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)
        handler = ContactEventHandler(
            config, mock_hub_link, on_hub_event=mock_hub_event_callback
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        # First: receive the request (this caches sender info)
        received_event = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-cached-123",
                from_handle="@alice",
                from_name="Alice Smith",
                message="Hi there!",
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )
        await handler.handle(received_event)

        # Clear call history to isolate the update event
        mock_hub_event_callback.reset_mock()

        # Clear dedup cache so update event is processed
        handler._processed_events.clear()

        # Second: receive the update (should use cached info)
        update_event = ContactRequestUpdatedEvent(
            payload=ContactRequestUpdatedPayload(
                id="req-cached-123",
                status="approved",
            )
        )
        await handler.handle(update_event)

        # Verify the update message includes the cached sender info
        call_args = mock_hub_event_callback.call_args
        message_event = call_args[0][1]
        content = message_event.payload.content

        assert "[Contact Request Update]" in content
        assert "Alice Smith" in content  # Cached name
        assert "@alice" in content  # Cached handle
        assert "approved" in content
        assert "req-cached-123" in content
