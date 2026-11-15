"""Fixtures for the shared CrewAI tool builder."""

from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from band.core.types import Capability
from band.testing.fake_tools import FakeAgentTools
from tests.crewaicontract import ToolFailure
from tests.integrations.crewaitools.kit import (
    MockBaseTool,
)


@pytest.fixture
def crewai_mocks(monkeypatch):
    mock_crewai_tools_module = MagicMock()
    mock_crewai_tools_module.BaseTool = MockBaseTool
    mock_crewai_tools_module.ToolFailure = ToolFailure
    mock_nest_asyncio = MagicMock()

    # `_nest_asyncio_applied` is process-global. Any test running with a mocked
    # nest_asyncio can flip it True without anything actually being patched, which
    # then silently disables the real patch for every later test — so isolate it.
    runtime = importlib.import_module("band.integrations.crewai.runtime")
    monkeypatch.setattr(runtime, "_nest_asyncio_applied", False)

    # No sys.modules surgery: every crewai import in the band modules is
    # TYPE_CHECKING-only or function-local, so they pick the mocks up at call time.
    monkeypatch.setitem(sys.modules, "crewai.tools", mock_crewai_tools_module)
    monkeypatch.setitem(sys.modules, "nest_asyncio", mock_nest_asyncio)

    yield mock_nest_asyncio


@pytest.fixture
def builder_mod(crewai_mocks):

    return importlib.import_module("band.integrations.crewai.tools")


@pytest.fixture
def runtime_mod(crewai_mocks):

    return importlib.import_module("band.integrations.crewai.runtime")


@pytest.fixture
def platform_args_schemas(builder_mod):
    """Tool name -> the args schema CrewAI actually advertises to the LLM."""

    tools = builder_mod.build_band_crewai_tools(
        get_context=lambda: None,
        reporter=builder_mod.NoopReporter(),
        capabilities=frozenset(
            {Capability.CONTACTS, Capability.MEMORY, Capability.FILES}
        ),
    )
    return {tool.name: tool.args_schema for tool in tools}


@pytest.fixture
def room_tools() -> FakeAgentTools:
    return FakeAgentTools()


@pytest.fixture
def tracker(builder_mod):
    return builder_mod.ReplyTracker()


@pytest.fixture
def crew_tools(builder_mod, room_tools, tracker):
    """The crew's tools by name, bound to ``room_tools`` as the room context."""

    def build(custom_tools: list[Any] | None = None) -> dict[str, Any]:
        context = builder_mod.CrewAIToolContext(
            room_id="room-1", tools=room_tools, reply_tracker=tracker
        )
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=builder_mod.NoopReporter(),
            custom_tools=custom_tools,
        )
        return {tool.name: tool for tool in tools}

    return build
