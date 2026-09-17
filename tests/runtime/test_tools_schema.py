"""Tests for the tool schema/dispatch-boundary helpers in band.runtime.tools.schema."""

from __future__ import annotations

import inspect
from typing import get_type_hints

from pydantic import BaseModel

from band.runtime.tools import (
    get_tool_docstring_with_args,
    platform_tool,
    serialize_tool_result,
)


class _Sub(BaseModel):
    id: str


class _Result(BaseModel):
    id: str
    sub: _Sub | None = None


class TestSerializeToolResult:
    def test_model_becomes_a_plain_dict(self) -> None:
        assert serialize_tool_result(_Result(id="r1")) == {"id": "r1", "sub": None}

    def test_list_of_models_becomes_a_list_of_dicts(self) -> None:
        result = serialize_tool_result([_Result(id="r1"), _Result(id="r2")])
        assert result == [{"id": "r1", "sub": None}, {"id": "r2", "sub": None}]

    def test_a_list_of_non_models_passes_through_unchanged(self) -> None:
        assert serialize_tool_result(["a", "b"]) == ["a", "b"]

    def test_a_plain_value_passes_through_unchanged(self) -> None:
        assert serialize_tool_result("already a string") == "already a string"
        assert serialize_tool_result({"id": "r1"}) == {"id": "r1"}


class TestPlatformTool:
    async def test_wraps_a_model_return_into_a_plain_dict(self) -> None:
        @platform_tool
        async def band_example(identifier: str) -> _Result | str:
            """Example tool.

            Args:
                identifier: an id
            """
            return _Result(id=identifier)

        result = await band_example("r1")

        assert result == {"id": "r1", "sub": None}

    async def test_an_error_string_passes_through_unchanged(self) -> None:
        @platform_tool
        async def band_example(identifier: str) -> _Result | str:
            """Example tool.

            Args:
                identifier: an id
            """
            return f"Error handling '{identifier}'"

        result = await band_example("r1")

        assert result == "Error handling 'r1'"

    def test_preserves_the_original_signature_for_framework_introspection(
        self,
    ) -> None:
        """pydantic-ai reads the wrapped signature/annotations to build a tool's
        argument schema -- wrapping the call must not hide them behind
        ``(*args, **kwargs)``."""

        @platform_tool
        async def band_example(identifier: str, count: int = 1) -> _Result | str:
            """Example tool.

            Args:
                identifier: an id
                count: how many
            """
            return _Result(id=identifier)

        assert list(inspect.signature(band_example).parameters) == [
            "identifier",
            "count",
        ]
        assert get_type_hints(band_example)["identifier"] is str
        assert get_type_hints(band_example)["count"] is int

    def test_injects_the_master_docstring(self) -> None:
        """Pre-existing behavior: unrelated to serialization, guarded here too
        since platform_tool now wraps the function rather than returning it
        unchanged."""

        @platform_tool
        async def band_example(identifier: str) -> _Result | str:
            """Original docstring, discarded."""
            return _Result(id=identifier)

        assert band_example.__doc__ == get_tool_docstring_with_args("band_example")
