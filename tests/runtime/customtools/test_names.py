"""Portable tool names derive from their input model names."""

from __future__ import annotations

from pydantic import BaseModel

from band.runtime.custom_tools import (
    get_custom_tool_name,
)
from tests.runtime.customtools.kit import (
    CalculatorInput,
    SearchWebInput,
    WeatherInput,
)


class TestGetCustomToolName:
    """Test tool name derivation from model class."""

    def test_removes_input_suffix(self) -> None:
        """Should remove 'Input' suffix and lowercase."""
        assert get_custom_tool_name(WeatherInput) == "weather"
        assert get_custom_tool_name(CalculatorInput) == "calculator"

    def test_handles_multi_word_names(self) -> None:
        """Should lowercase multi-word names."""
        assert get_custom_tool_name(SearchWebInput) == "searchweb"

    def test_handles_no_input_suffix(self) -> None:
        """Should just lowercase if no 'Input' suffix."""

        class MyTool(BaseModel):
            value: str

        assert get_custom_tool_name(MyTool) == "mytool"
