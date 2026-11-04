"""BuildADKTools behavior of the Google ADK adapter."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tests.adapters.googleadk.bridge import GoogleADKAdapter


class TestBuildADKTools:
    """Tests for _build_adk_tools."""

    def test_builds_platform_tools(self, mock_tools):
        """Should create tool bridges from platform tool schemas."""
        adapter = GoogleADKAdapter()
        bridges = adapter._build_adk_tools(mock_tools)

        assert len(bridges) == 1
        assert bridges[0].name == "band_send_message"

    def test_includes_custom_tools(self, mock_tools):
        """Should include custom tools in the bridge list."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = GoogleADKAdapter(additional_tools=[(EchoInput, echo)])
        bridges = adapter._build_adk_tools(mock_tools)

        assert len(bridges) == 2
        tool_names = [b.name for b in bridges]
        assert "band_send_message" in tool_names
        assert "echo" in tool_names
