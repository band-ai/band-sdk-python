"""BuildGeminiTools behavior for Gemini."""

from __future__ import annotations

from unittest.mock import MagicMock

from band.adapters.gemini import GeminiAdapter


class TestBuildGeminiTools:
    """Gemini rejects numeric bounds and additionalProperties on tool params, so
    the adapter must sanitize Band schemas before building declarations."""

    def test_declarations_drop_numeric_bounds_and_additional_properties(
        self, mock_tools
    ):
        mock_tools.get_openai_tool_schemas = MagicMock(
            return_value=[
                {
                    "type": "function",
                    "function": {
                        "name": "band_lookup_peers",
                        "description": "lookup peers",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "page_size": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ]
        )
        adapter = GeminiAdapter()

        tools = adapter._build_gemini_tools(mock_tools)

        decl = tools[0].function_declarations[0]
        schema = decl.parameters_json_schema
        assert "additionalProperties" not in schema
        page_size = schema["properties"]["page_size"]
        assert "minimum" not in page_size
        assert "maximum" not in page_size
