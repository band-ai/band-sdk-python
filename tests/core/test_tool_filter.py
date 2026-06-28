"""Tests for filter_tool_schemas and sanitize_tool_schema helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from band.core.tool_filter import filter_tool_schemas, sanitize_tool_schema
from band.core.types import AdapterFeatures


@dataclass
class _FakeTool:
    name: str
    category: str | None = None


def _get_name(t: _FakeTool) -> str:
    return t.name


def _get_category(t: _FakeTool) -> str | None:
    return t.category


SAMPLE_TOOLS = [
    _FakeTool("band_send_message", "chat"),
    _FakeTool("band_lookup_peers", "chat"),
    _FakeTool("band_store_memory", "memory"),
    _FakeTool("band_list_contacts", "contact"),
]


class TestFilterToolSchemas:
    def test_empty_features_passes_everything(self) -> None:
        result = filter_tool_schemas(
            SAMPLE_TOOLS, AdapterFeatures(), get_name=_get_name
        )
        assert result == SAMPLE_TOOLS

    def test_include_tools_filters(self) -> None:
        f = AdapterFeatures(include_tools=["band_send_message"])
        result = filter_tool_schemas(SAMPLE_TOOLS, f, get_name=_get_name)
        assert len(result) == 1
        assert result[0].name == "band_send_message"

    def test_exclude_tools_filters(self) -> None:
        f = AdapterFeatures(exclude_tools=["band_store_memory"])
        result = filter_tool_schemas(SAMPLE_TOOLS, f, get_name=_get_name)
        assert len(result) == 3
        assert all(t.name != "band_store_memory" for t in result)

    def test_include_categories_filters(self) -> None:
        f = AdapterFeatures(include_categories=["chat"])
        result = filter_tool_schemas(
            SAMPLE_TOOLS, f, get_name=_get_name, get_category=_get_category
        )
        assert len(result) == 2
        assert all(t.category == "chat" for t in result)

    def test_include_categories_without_getter_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        f = AdapterFeatures(include_categories=["chat"])
        with caplog.at_level(logging.WARNING):
            result = filter_tool_schemas(SAMPLE_TOOLS, f, get_name=_get_name)
        assert "does not support category filtering" in caplog.text
        # All tools pass through when category filtering is unsupported
        assert len(result) == 4

    def test_unknown_include_tool_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        f = AdapterFeatures(include_tools=["band_nonexistent", "band_send_message"])
        with caplog.at_level(logging.WARNING):
            result = filter_tool_schemas(SAMPLE_TOOLS, f, get_name=_get_name)
        assert "unknown names" in caplog.text
        assert len(result) == 1

    def test_unknown_exclude_tool_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        f = AdapterFeatures(exclude_tools=["band_nonexistent"])
        with caplog.at_level(logging.WARNING):
            result = filter_tool_schemas(SAMPLE_TOOLS, f, get_name=_get_name)
        assert "unknown names" in caplog.text
        assert len(result) == 4

    def test_include_and_exclude_combined(self) -> None:
        f = AdapterFeatures(
            include_tools=["band_send_message", "band_lookup_peers"],
            exclude_tools=["band_lookup_peers"],
        )
        result = filter_tool_schemas(SAMPLE_TOOLS, f, get_name=_get_name)
        assert len(result) == 1
        assert result[0].name == "band_send_message"

    def test_empty_schemas_returns_empty(self) -> None:
        f = AdapterFeatures(include_tools=["band_send_message"])
        result = filter_tool_schemas([], f, get_name=_get_name)
        assert result == []

    def test_category_then_include_precedence_yields_empty(self) -> None:
        """Categories filter first, so include_tools on a tool outside that
        category still produces an empty result."""
        f = AdapterFeatures(
            include_categories=["chat"],
            include_tools=["band_store_memory"],
        )
        result = filter_tool_schemas(
            SAMPLE_TOOLS, f, get_name=_get_name, get_category=_get_category
        )
        # band_store_memory is category "memory", excluded by categories step
        assert result == []


class TestSanitizeToolSchema:
    """sanitize_tool_schema drops provider-incompatible JSON-Schema keywords."""

    def test_drops_numeric_bounds_recursively(self):
        schema = {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1},
                "page_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "multipleOf": 1,
                },
                "name": {"type": "string"},
            },
        }

        cleaned = sanitize_tool_schema(schema, drop_numeric_bounds=True)

        assert cleaned["properties"]["page"] == {"type": "integer"}
        assert cleaned["properties"]["page_size"] == {"type": "integer"}
        assert cleaned["properties"]["name"] == {"type": "string"}

    def test_drops_additional_properties_recursively(self):
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }

        cleaned = sanitize_tool_schema(schema, drop_additional_properties=True)

        assert "additionalProperties" not in cleaned
        assert "additionalProperties" not in cleaned["properties"]["nested"]

    def test_default_is_a_noop_copy(self):
        schema = {"type": "integer", "maximum": 100, "additionalProperties": False}

        cleaned = sanitize_tool_schema(schema)

        assert cleaned == schema
        assert cleaned is not schema

    def test_does_not_mutate_input(self):
        schema = {"type": "integer", "minimum": 1, "maximum": 100}

        sanitize_tool_schema(schema, drop_numeric_bounds=True)

        assert schema == {"type": "integer", "minimum": 1, "maximum": 100}

    def test_preserves_property_literally_named_maximum(self):
        # ``maximum`` as a property *name* (under ``properties``) is a field, not
        # the numeric-bound keyword, and must survive the strip.
        schema = {
            "type": "object",
            "properties": {
                "maximum": {"type": "integer", "maximum": 10},
                "minimum": {"type": "string"},
            },
        }

        cleaned = sanitize_tool_schema(schema, drop_numeric_bounds=True)

        assert set(cleaned["properties"]) == {"maximum", "minimum"}
        # The bound keyword *inside* the "maximum" field's subschema is stripped.
        assert cleaned["properties"]["maximum"] == {"type": "integer"}

    def test_recurses_into_lists_and_returns_non_dicts_as_is(self):
        schema = {
            "anyOf": [
                {"type": "integer", "maximum": 5},
                {"type": "string"},
            ],
        }

        cleaned = sanitize_tool_schema(schema, drop_numeric_bounds=True)

        assert cleaned == {"anyOf": [{"type": "integer"}, {"type": "string"}]}
        assert sanitize_tool_schema("x", drop_numeric_bounds=True) == "x"
        assert sanitize_tool_schema(None) is None
