"""Tests for ContactEventConfig and ContactEventStrategy."""

import pytest

from band.runtime.types import ContactEventConfig, ContactEventStrategy


class TestContactEventStrategy:
    """Tests for ContactEventStrategy enum."""

    def test_disabled_is_default_value(self):
        """DISABLED should be the default strategy value."""
        assert ContactEventStrategy.DISABLED.value == "disabled"

    def test_all_strategies_have_values(self):
        """All strategies should have string values."""
        assert ContactEventStrategy.DISABLED.value == "disabled"
        assert ContactEventStrategy.CALLBACK.value == "callback"
        assert ContactEventStrategy.HUB_ROOM.value == "hub_room"


class TestContactEventConfigDefaults:
    """Tests for ContactEventConfig default values."""

    def test_default_config_is_disabled(self):
        """Verify default strategy is DISABLED."""
        config = ContactEventConfig()

        assert config.strategy == ContactEventStrategy.DISABLED

    def test_broadcast_changes_default_false(self):
        """Verify default broadcast_changes=False."""
        config = ContactEventConfig()

        assert config.broadcast_changes is False

    def test_hub_room_uses_default_task_id(self):
        """Verify default hub_task_id is None (backend expects UUID or None)."""
        config = ContactEventConfig()

        assert config.hub_task_id is None


class TestContactEventConfigValidation:
    """Tests for ContactEventConfig validation."""

    def test_callback_requires_on_event(self):
        """Verify ValueError if CALLBACK without on_event."""
        with pytest.raises(ValueError, match="CALLBACK strategy requires on_event"):
            ContactEventConfig(strategy=ContactEventStrategy.CALLBACK)

    def test_callback_with_on_event_valid(self):
        """Verify CALLBACK with on_event works."""

        async def handler(event, tools):
            pass

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=handler,
        )

        assert config.strategy == ContactEventStrategy.CALLBACK
        assert config.on_event is handler

    def test_hub_room_custom_task_id(self):
        """Verify custom hub_task_id (must be UUID format)."""
        task_uuid = "550e8400-e29b-41d4-a716-446655440000"
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            hub_task_id=task_uuid,
        )

        assert config.hub_task_id == task_uuid


class TestContactEventConfigComposability:
    """Tests for broadcast_changes composability with all strategies."""

    def test_broadcast_changes_composable_with_disabled(self):
        """DISABLED + broadcast_changes=True should be valid."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )

        assert config.strategy == ContactEventStrategy.DISABLED
        assert config.broadcast_changes is True

    def test_broadcast_changes_composable_with_callback(self):
        """CALLBACK + broadcast_changes=True should be valid."""

        async def handler(event, tools):
            pass

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=handler,
            broadcast_changes=True,
        )

        assert config.strategy == ContactEventStrategy.CALLBACK
        assert config.broadcast_changes is True

    def test_broadcast_changes_composable_with_hub_room(self):
        """HUB_ROOM + broadcast_changes=True should be valid."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            broadcast_changes=True,
        )

        assert config.strategy == ContactEventStrategy.HUB_ROOM
        assert config.broadcast_changes is True
