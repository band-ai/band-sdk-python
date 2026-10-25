"""A host that cannot run a batch refuses consume_all_queued at construction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.exceptions import BandConfigError
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.helpers import AGENT_ID, ROOM_ID

BATCHING = SessionConfig(consume_all_queued=True)


class TestUnsupportedHosts:
    def test_an_agent_runtime_without_a_batch_callback_is_refused(self) -> None:
        with pytest.raises(BandConfigError):
            AgentRuntime(
                link=MagicMock(),
                agent_id=AGENT_ID,
                on_execute=AsyncMock(),
                session_config=BATCHING,
            )

    async def test_a_custom_execution_factory_is_refused(self) -> None:
        with pytest.raises(BandConfigError, match="execution_factory"):
            AgentRuntime(
                MagicMock(),
                AGENT_ID,
                AsyncMock(),
                execution_factory=MagicMock(),
                session_config=BATCHING,
            )

    async def test_an_execution_without_a_batch_callback_is_refused(self) -> None:
        with pytest.raises(BandConfigError, match="on_execute_batch"):
            ExecutionContext(
                ROOM_ID, MagicMock(), AsyncMock(), config=BATCHING, agent_id=AGENT_ID
            )
