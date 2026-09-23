"""Walking a paged chat listing without trusting it to end."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from band.runtime.tools import CHAT_PAGE_SIZE, MAX_CHAT_PAGES, iter_chat_pages


def listing(total_pages: int | None) -> Any:
    return SimpleNamespace(
        data=[],
        metadata=SimpleNamespace(total_pages=total_pages),
    )


async def walk(fetch: Any) -> list[int]:
    """The pages actually requested, in order."""
    requested: list[int] = []

    async def record(page: int, page_size: int) -> Any:
        assert page_size == CHAT_PAGE_SIZE
        requested.append(page)
        return fetch(page)

    async for _ in iter_chat_pages(record):
        pass
    return requested


async def test_it_reads_every_page_the_listing_reports() -> None:
    assert await walk(lambda page: listing(3)) == [1, 2, 3]


async def test_it_stops_at_one_page_when_the_count_is_missing() -> None:
    """An absent count is not a licence to keep asking."""
    assert await walk(lambda page: listing(None)) == [1]


async def test_a_listing_that_never_ends_is_capped_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Otherwise a bad page count spins forever against the platform."""
    with caplog.at_level(logging.WARNING):
        requested = await walk(lambda page: listing(page + 1))

    assert requested == list(range(1, MAX_CHAT_PAGES + 1))
    assert "page cap" in caplog.text, "a silent truncation would hide missing rooms"
