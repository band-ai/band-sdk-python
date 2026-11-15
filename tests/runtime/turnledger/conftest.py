"""Room-bound tools for turn-ledger behavior."""

from __future__ import annotations

from typing import Any

import pytest

from band.runtime.tools import AgentTools
from tests.runtime.turnledger.kit import ALICE


@pytest.fixture
def tools(mock_rest_client: Any) -> AgentTools:
    return AgentTools("room-1", mock_rest_client, participants=[ALICE])
