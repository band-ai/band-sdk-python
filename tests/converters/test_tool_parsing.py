"""Tests for shared tool parsing utilities."""

from thenvoi.converters._tool_parsing import (
    ParsedToolCall,
    ParsedToolResult,
    parse_tool_call,
    parse_tool_result,
)


class TestParseToolCall:
    """Tests for parse_tool_call function."""

    def test_parses_valid_tool_call(self):
        """Valid JSON with all required fields is parsed correctly."""
        content = (
            '{"name": "search", "args": {"query": "test"}, "tool_call_id": "call_123"}'
        )

        result = parse_tool_call(content)

        assert result is not None
        assert isinstance(result, ParsedToolCall)
        assert result.name == "search"
        assert result.args == {"query": "test"}
        assert result.tool_call_id == "call_123"

    def test_handles_empty_args(self):
        """Tool call with no args defaults to empty dict."""
        content = '{"name": "get_time", "tool_call_id": "call_123"}'

        result = parse_tool_call(content)

        assert result is not None
        assert result.args == {}

    def test_returns_none_for_invalid_json(self, caplog):
        """Invalid JSON returns None with warning."""
        content = "not valid json"

        result = parse_tool_call(content)

        assert result is None
        assert "Failed to parse tool_call" in caplog.text

    def test_returns_none_for_missing_tool_call_id(self, caplog):
        """Missing tool_call_id returns None with warning."""
        content = '{"name": "search", "args": {}}'

        result = parse_tool_call(content)

        assert result is None
        assert "missing tool_call_id" in caplog.text

    def test_returns_none_for_missing_name(self, caplog):
        """Missing name returns None with warning."""
        content = '{"args": {}, "tool_call_id": "call_123"}'

        result = parse_tool_call(content)

        assert result is None
        assert "missing name" in caplog.text

    def test_handles_nested_args(self):
        """Handles complex nested arguments."""
        content = '{"name": "api_call", "args": {"nested": {"key": "value"}, "list": [1, 2, 3]}, "tool_call_id": "call_123"}'

        result = parse_tool_call(content)

        assert result is not None
        assert result.args == {"nested": {"key": "value"}, "list": [1, 2, 3]}

    def test_returns_none_for_non_object_json(self, caplog):
        """Non-dict JSON payloads are malformed tool events."""
        result = parse_tool_call('["not", "an", "object"]')

        assert result is None
        assert "non-object payload" in caplog.text

    def test_uses_empty_args_for_non_object_args(self, caplog):
        """Malformed args should not crash history conversion."""
        content = '{"name": "search", "args": ["bad"], "tool_call_id": "call_123"}'

        result = parse_tool_call(content)

        assert result is not None
        assert result.args == {}
        assert "args were not an object" in caplog.text


class TestParseToolResult:
    """Tests for parse_tool_result function."""

    def test_parses_valid_tool_result(self):
        """Valid JSON with all required fields is parsed correctly."""
        content = (
            '{"name": "search", "output": "result data", "tool_call_id": "call_123"}'
        )

        result = parse_tool_result(content)

        assert result is not None
        assert isinstance(result, ParsedToolResult)
        assert result.name == "search"
        assert result.output == "result data"
        assert result.tool_call_id == "call_123"
        assert result.is_error is False

    def test_handles_is_error_true(self):
        """is_error=True is preserved."""
        content = '{"name": "search", "output": "Error occurred", "tool_call_id": "call_123", "is_error": true}'

        result = parse_tool_result(content)

        assert result is not None
        assert result.is_error is True

    def test_handles_is_error_false(self):
        """is_error=False is preserved."""
        content = '{"name": "search", "output": "result", "tool_call_id": "call_123", "is_error": false}'

        result = parse_tool_result(content)

        assert result is not None
        assert result.is_error is False

    def test_defaults_is_error_to_false(self):
        """Missing is_error defaults to False."""
        content = '{"name": "search", "output": "result", "tool_call_id": "call_123"}'

        result = parse_tool_result(content)

        assert result is not None
        assert result.is_error is False

    def test_handles_empty_output(self):
        """Tool result with no output defaults to empty string."""
        content = '{"name": "action", "tool_call_id": "call_123"}'

        result = parse_tool_result(content)

        assert result is not None
        assert result.output == ""

    def test_converts_non_string_output_to_string(self):
        """Non-string output is converted to string."""
        content = '{"name": "calc", "output": 42, "tool_call_id": "call_123"}'

        result = parse_tool_result(content)

        assert result is not None
        assert result.output == "42"

    def test_returns_none_for_invalid_json(self, caplog):
        """Invalid JSON returns None with warning."""
        content = "not valid json"

        result = parse_tool_result(content)

        assert result is None
        assert "Failed to parse tool_result" in caplog.text

    def test_returns_none_for_missing_tool_call_id(self, caplog):
        """Missing tool_call_id returns None with warning."""
        content = '{"name": "search", "output": "result"}'

        result = parse_tool_result(content)

        assert result is None
        assert "missing tool_call_id" in caplog.text

    def test_returns_none_for_missing_name(self, caplog):
        """Missing name returns None with warning."""
        content = '{"output": "result", "tool_call_id": "call_123"}'

        result = parse_tool_result(content)

        assert result is None
        assert "missing name" in caplog.text

    def test_returns_none_for_non_object_json(self, caplog):
        """Non-dict JSON payloads are malformed tool result events."""
        result = parse_tool_result('["not", "an", "object"]')

        assert result is None
        assert "non-object payload" in caplog.text
