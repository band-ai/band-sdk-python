"""Fixtures for Agent contact event wiring tests."""

from __future__ import annotations

import pytest

from tests.agentcontacts.kit import ContactAdapter


@pytest.fixture
def contact_adapter() -> ContactAdapter:
    """Exercise contact wiring with the adapter's real construction and hooks."""
    return ContactAdapter()
