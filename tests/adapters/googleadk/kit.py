"""Yield an empty native event stream for isolated formatting tests."""

from __future__ import annotations


async def _empty_async_iter(**kwargs):
    return
    yield  # Make it an async generator
