"""Input for a provider tool round trip."""

from __future__ import annotations

from pydantic import BaseModel


class RecordInput(BaseModel):
    """Record a note."""

    note: str
