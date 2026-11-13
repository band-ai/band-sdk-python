"""MCP engine behavior."""

from __future__ import annotations

import pytest

from band.integrations.mcp.engine import (
    build_tool_registration,
    validate_unique_tool_names,
)
from band.runtime.tools import TOOL_DEFINITIONS
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.engine.peers import _agent_resolver


class TestValidateUniqueToolNames:
    def test_raises_on_duplicate_across_registrations(self) -> None:
        definition = TOOL_DEFINITIONS["band_create_chatroom"]
        registration = build_tool_registration(
            definition,
            definition.input_model,
            resolver=_agent_resolver(FakeAgentTools()),
            strip_chat_id=False,
        )
        with pytest.raises(ValueError, match="Duplicate MCP tool names"):
            validate_unique_tool_names([registration, registration])
