"""Shared Python testing utilities for Band repositories.

This package provides:
- factories: Mock data factories for creating test objects
- factories.events: Event factory helpers (make_message_event, etc.)
- fixtures: Pytest fixtures (mock_api_client, sample_room_message, etc.)
- fakes: Fake implementations (FakeAgentTools, FakePhoenixServer)
- markers: Pytest skip markers (requires_api, requires_multi_agent, etc.)
- pagination: Helpers for paginated API responses in integration tests
- streaming: WebSocket payload types (MessageCreatedPayload, etc.)

Usage:
    # In your conftest.py (fixtures auto-registered via pytest plugin)
    pytest_plugins = ["band_testing.fixtures"]

    # In your tests
    from band_testing.factories import factory
    from band_testing.factories.events import make_message_event
    from band_testing.fakes import FakeAgentTools
    from band_testing.markers import requires_api
    from band_testing.pagination import fetch_all_pages, find_item_in_pages
    from band_testing.streaming import MessageCreatedPayload
"""

from __future__ import annotations

from band_testing.pagination import (
    fetch_all_pages,
    find_item_in_pages,
    item_exists_in_pages,
)

__version__ = "0.1.1"

__all__ = [
    "fetch_all_pages",
    "find_item_in_pages",
    "item_exists_in_pages",
]
