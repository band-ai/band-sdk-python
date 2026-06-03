"""Base utilities for mock data factories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock


def make_uuid() -> str:
    """Generate a random UUID string."""
    return str(uuid.uuid4())


def make_timestamp() -> datetime:
    """Generate current UTC timestamp."""
    return datetime.now(UTC)


def make_pydantic_mock(**attrs: object) -> Mock:
    """Create a Mock that behaves like a Pydantic model.

    Adds model_dump() method that returns the attributes as a dict.

    Args:
        **attrs: Attributes to set on the mock object.

    Returns:
        A Mock object with model_dump() method.
    """
    mock = Mock()
    for key, value in attrs.items():
        setattr(mock, key, value)

    # Add model_dump to simulate Pydantic BaseModel behavior
    def model_dump(**kwargs: object) -> dict[str, object]:
        return dict(attrs)

    mock.model_dump = model_dump
    return mock
