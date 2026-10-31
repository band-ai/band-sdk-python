"""Shared tool definitions for these behavioral tests."""

from __future__ import annotations

from pydantic import BaseModel

ALICE = {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"}


class LookupInput(BaseModel):
    """Look something up."""

    topic: str
