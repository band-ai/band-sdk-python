"""Tests for filter_tool_schemas helper."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from band.core.tool_filter import filter_tool_schemas
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
