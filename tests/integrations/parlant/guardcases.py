"""Malformed upstream payload and log projections for tool guard tests."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError


class RecordInput(BaseModel):
    """Store a record."""

    count: int


async def store_upstream_record(args: RecordInput) -> str:
    """Fails validating an upstream payload: a handler bug, not a bad argument."""
    try:
        RecordInput.model_validate({"count": "many"})
    except ValidationError as exc:
        raise RuntimeError("upstream returned a malformed record") from exc
    return "stored"


def lines_at(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """The captured log messages logged at exactly *level*."""
    return [record.getMessage() for record in caplog.records if record.levelno == level]
