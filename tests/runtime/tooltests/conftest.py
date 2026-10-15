from __future__ import annotations

import pytest


@pytest.fixture
def participants() -> list[dict[str, str]]:
    """Sample participants list."""
    return [
        {"id": "user-1", "name": "User One", "type": "User", "handle": "@user-one"},
        {"id": "user-2", "name": "User Two", "type": "User", "handle": "@user-two"},
    ]
