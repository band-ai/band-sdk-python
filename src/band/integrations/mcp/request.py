"""Internal request metadata for provider correlation, outside tool arguments."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_metadata: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "band_mcp_metadata", default=None
)


def request_metadata() -> Mapping[str, Any]:
    return _metadata.get() or {}


@contextmanager
def request_context(metadata: Mapping[str, Any]) -> Iterator[None]:
    token = _metadata.set(metadata)
    try:
        yield
    finally:
        _metadata.reset(token)
