"""Fixtures shared by the delivery suites."""

from __future__ import annotations

import pytest

from tests.runtime.delivery.kit import RestPeer


@pytest.fixture
def rest_peer() -> RestPeer:
    return RestPeer()
