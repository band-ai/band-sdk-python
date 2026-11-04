"""Build real CrewAI BaseTool models without optional-import contamination."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from band.core.types import AdapterFeatures, Capability
from band.integrations.crewai.tools import NoopReporter, build_band_crewai_tools
from tests.integrations.crewai.realtools.kit import (
    EXPECTED_BASE_TOOLS,
    build_base_tools,
)


class TestRealToolBuild:
    def test_platform_tools_build_against_the_real_base_tool(self) -> None:
        """The tool models resolve their annotations and instantiate."""
        tools = build_base_tools()

        assert len(tools) == EXPECTED_BASE_TOOLS
        assert "band_send_message" in {tool.name for tool in tools}

    def test_file_tools_build_against_the_real_base_tool(self) -> None:
        """The three room-file tool models also resolve and instantiate for real --
        same "not fully defined" failure mode this file exists to catch."""
        tools = build_band_crewai_tools(
            get_context=lambda: None,
            reporter=NoopReporter(),
            features=AdapterFeatures(capabilities={Capability.FILES}),
        )

        names = {tool.name for tool in tools}
        assert {
            "band_list_room_files",
            "band_read_room_file",
            "band_send_room_file",
        }.issubset(names)

    def test_a_mocked_crewai_window_does_not_poison_a_later_real_build(self) -> None:
        """Faking crewai for one test must leave the next one a working real package.

        The regression this guards: tests that mocked crewai also evicted the
        band.integrations.crewai modules and never restored them, after which the tool
        models could no longer resolve their annotations — pydantic resolves them through
        ``cls.__module__``, and that name no longer pointed at a live module.
        """
        fake_tools = MagicMock()
        fake_tools.BaseTool = type("BaseTool", (), {})

        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(sys.modules, "crewai.tools", fake_tools)
            assert len(build_base_tools()) == EXPECTED_BASE_TOOLS

        assert len(build_base_tools()) == EXPECTED_BASE_TOOLS
