"""MCP failurelogging behavior."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from band.integrations.mcp.engine import (
    EngineSpec,
    MCPToolRegistration,
    build_engine,
)
from tests.mcp.engine.models import EchoInput


class TestFailureLogging:
    @pytest.mark.asyncio
    async def test_unexpected_tool_failure_is_logged(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unexpected execute failures keep a stack in agent logs."""

        async def execute(_arguments: dict[str, Any]) -> str:
            raise RuntimeError("boom")

        registration = MCPToolRegistration(
            name="echo", description="Echo", input_model=EchoInput, execute=execute
        )
        mcp = build_engine(EngineSpec(name="test-fail-log", tools=(registration,)))

        with caplog.at_level(logging.ERROR, logger="band.integrations.mcp.engine"):
            async with create_connected_server_and_client_session(mcp) as session:
                result = await session.call_tool("echo", {"message": "hi"})

        assert result.isError
        record = next(r for r in caplog.records if r.message == "echo failed")
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None
