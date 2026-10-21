from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.testing.activity import ActivityRecorder
from tests.runtime.helpers import ROOM_ID


@pytest.fixture
def recorder() -> ActivityRecorder:
    return ActivityRecorder()


@pytest.fixture
def tools(mock_rest_client: MagicMock, recorder: ActivityRecorder) -> AgentTools:
    """Real Band tools whose turn observer is the recorder."""
    tools = AgentTools(ROOM_ID, mock_rest_client)
    tools._activity_observer = recorder
    return tools
