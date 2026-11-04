"""Construct the real CrewAI chat tool surface."""

from __future__ import annotations

from crewai.tools import BaseTool

from band.core.types import AdapterFeatures
from band.integrations.crewai.tools import NoopReporter, build_band_crewai_tools
from band.runtime.tools import CHAT_TOOL_NAMES

EXPECTED_BASE_TOOLS = len(CHAT_TOOL_NAMES)


def build_base_tools() -> list[BaseTool]:
    return build_band_crewai_tools(
        get_context=lambda: None, reporter=NoopReporter(), features=AdapterFeatures()
    )
