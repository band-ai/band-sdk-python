"""Pagination utilities for integration tests against Band APIs.

Provides helper functions for working with paginated API responses
in integration tests.

Usage:
    from band_testing.pagination import fetch_all_pages, find_item_in_pages

    # Fetch all items from a paginated endpoint
    all_peers = fetch_all_pages(ctx, list_agent_peers)

    # Find a specific item
    room = find_item_in_pages(
        ctx, list_agent_chats,
        lambda item: item.get("title") == "My Room"
    )

    # Check if an item exists
    exists = item_exists_in_pages(ctx, list_agent_chats, room_id)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def fetch_all_pages(
    ctx: Any,
    list_func: Callable[..., str],
    page_size: int = 50,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Fetch all pages of results from a paginated endpoint.

    Args:
        ctx: Integration context (passed to list_func as first argument)
        list_func: The MCP tool function to call (e.g., list_agent_peers).
            Must accept ctx, page, page_size as arguments and return JSON string.
        page_size: Number of items per page
        **kwargs: Additional arguments to pass to the list function

    Returns:
        List of all items across all pages

    Example:
        all_peers = fetch_all_pages(ctx, list_agent_peers)
        all_chats = fetch_all_pages(ctx, list_agent_chats, page_size=100)
    """
    all_items: list[dict[str, Any]] = []
    page = 1

    while True:
        result = list_func(ctx, page=page, page_size=page_size, **kwargs)
        parsed = json.loads(result)

        items = parsed.get("data", [])
        all_items.extend(items)

        # Check if there are more pages
        metadata = parsed.get("metadata", {})
        total_pages = metadata.get("total_pages", 1)

        if page >= total_pages:
            break
        page += 1

    return all_items


def find_item_in_pages(
    ctx: Any,
    list_func: Callable[..., str],
    predicate: Callable[[dict[str, Any]], bool],
    page_size: int = 50,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Search through paginated results to find an item matching a predicate.

    Searches page by page and returns as soon as a matching item is found,
    avoiding fetching unnecessary pages.

    Args:
        ctx: Integration context (passed to list_func as first argument)
        list_func: The MCP tool function to call
        predicate: Function that returns True for the desired item
        page_size: Number of items per page
        **kwargs: Additional arguments to pass to the list function

    Returns:
        The matching item or None if not found

    Example:
        # Find a room by title
        room = find_item_in_pages(
            ctx, list_agent_chats,
            lambda item: item.get("title") == "My Room"
        )

        # Find a peer by name
        peer = find_item_in_pages(
            ctx, list_agent_peers,
            lambda item: item.get("name") == "DataAnalyst"
        )
    """
    page = 1

    while True:
        result = list_func(ctx, page=page, page_size=page_size, **kwargs)
        parsed = json.loads(result)

        items = parsed.get("data", [])
        for item in items:
            if predicate(item):
                return item

        metadata = parsed.get("metadata", {})
        total_pages = metadata.get("total_pages", 1)

        if page >= total_pages:
            break
        page += 1

    return None


def item_exists_in_pages(
    ctx: Any,
    list_func: Callable[..., str],
    item_id: str,
    page_size: int = 50,
    **kwargs: Any,
) -> bool:
    """Check if an item with given ID exists in paginated results.

    Convenience wrapper around find_item_in_pages for ID lookups.

    Args:
        ctx: Integration context (passed to list_func as first argument)
        list_func: The MCP tool function to call
        item_id: The ID to search for
        page_size: Number of items per page
        **kwargs: Additional arguments to pass to the list function

    Returns:
        True if item exists, False otherwise

    Example:
        if item_exists_in_pages(ctx, list_agent_chats, room_id):
            print("Room exists!")
    """
    return (
        find_item_in_pages(
            ctx, list_func, lambda item: item.get("id") == item_id, page_size, **kwargs
        )
        is not None
    )
