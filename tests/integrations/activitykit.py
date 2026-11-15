"""A custom tool shared by the framework activity suites."""

from __future__ import annotations

from pydantic import BaseModel


class LookupInput(BaseModel):
    """Look up a private value."""

    note: str
