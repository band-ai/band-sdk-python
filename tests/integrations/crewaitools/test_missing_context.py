"""MissingContext behavior at the CrewAI tool boundary."""

from __future__ import annotations

from tests.crewaicontract import tool_result_data


class TestMissingContext:
    def test_tool_returns_error_json_when_get_context_returns_none(self, builder_mod):
        tools = builder_mod.build_band_crewai_tools(
            get_context=lambda: None,
            reporter=builder_mod.NoopReporter(),
            capabilities=frozenset(),
        )
        send_message_tool = next(t for t in tools if t.name == "band_send_message")
        result_str = send_message_tool._run(content="hi", mentions=[])
        result = tool_result_data(result_str)
        assert result["status"] == "error"
        assert "No room context available" in result["message"]
