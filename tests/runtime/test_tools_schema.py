"""Tests for the tool schema/dispatch-boundary helpers in band.runtime.tools.schema."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, date, datetime
from typing import get_type_hints

import pytest
from pydantic import BaseModel

from band.client.rest import AgentContact
from band.runtime.tools import (
    GetMemoryInput,
    get_tool_docstring_with_args,
    platform_tool,
    serialize_tool_result,
    tool_result_text,
    validate_tool_arguments,
)


class _Sub(BaseModel):
    id: str


class _Result(BaseModel):
    id: str
    sub: _Sub | None = None


class _Thumbnail(BaseModel):
    png: bytes = b"\x89PNG"


class _Opaque:
    def __str__(self) -> str:
        return "opaque"


class _MemoryResult(BaseModel):
    content: str
    inserted_at: datetime


class TestSerializeToolResult:
    def test_model_becomes_a_plain_dict(self) -> None:
        assert serialize_tool_result(_Result(id="r1")) == {"id": "r1", "sub": None}

    def test_list_of_models_becomes_a_list_of_dicts(self) -> None:
        result = serialize_tool_result([_Result(id="r1"), _Result(id="r2")])
        assert result == [{"id": "r1", "sub": None}, {"id": "r2", "sub": None}]

    def test_timestamped_memory_results_are_json_serializable(self) -> None:
        result = _MemoryResult(
            content="opaque-marker", inserted_at=datetime(2026, 9, 28, tzinfo=UTC)
        )

        assert json.loads(json.dumps(serialize_tool_result(result))) == {
            "content": "opaque-marker",
            "inserted_at": "2026-09-28T00:00:00Z",
        }

    def test_a_list_of_non_models_passes_through_unchanged(self) -> None:
        assert serialize_tool_result(["a", "b"]) == ["a", "b"]

    def test_a_plain_value_passes_through_unchanged(self) -> None:
        assert serialize_tool_result("already a string") == "already a string"
        assert serialize_tool_result({"id": "r1"}) == {"id": "r1"}


@pytest.mark.parametrize(
    ("result", "text"),
    [
        ("already text", "already text"),
        ({date(2026, 5, 4): 2}, '{"2026-05-04": 2}'),
        ({"png": b"\x89PNG"}, '{"png": "iVBORw=="}'),
        ({"thumb": _Thumbnail()}, '{"thumb": {"png": "iVBORw=="}}'),
        ({"odd": _Opaque()}, '{"odd": "opaque"}'),
    ],
    ids=[
        "text",
        "non-text-keys",
        "binary-bytes",
        "binary-bytes-in-a-nested-model",
        "unserializable-object",
    ],
)
def test_tool_result_text_renders_any_result_a_tool_returned(result, text) -> None:
    """The handler already ran, so rendering its result must never fail."""
    assert tool_result_text(result) == text


def test_tool_result_text_keeps_a_platform_models_unset_fields() -> None:
    """A platform model renders through its own dump, so the model reads every
    field, unset ones included."""
    contact = AgentContact(
        id="c1",
        handle="bob",
        name="Bob",
        inserted_at="2025-01-01T00:00:00Z",
        type="Agent",
        online=True,
    )

    assert json.loads(tool_result_text(contact)) == {
        "avatar_url": None,
        "description": None,
        "handle": "bob",
        "id": "c1",
        "inserted_at": "2025-01-01T00:00:00Z",
        "is_external": None,
        "listed_in_directory": None,
        "name": "Bob",
        "online": True,
        "tags": None,
        "type": "Agent",
    }


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


@pytest.mark.parametrize("value", ["", " ", ".", "#"])
def test_path_id_feedback_identifies_tool_and_field(value: str) -> None:
    with pytest.raises(
        ValueError, match="Invalid arguments for band_get_memory: memory_id:"
    ):
        validate_tool_arguments("band_get_memory", GetMemoryInput, {"memory_id": value})


def test_path_id_normalization_preserves_text() -> None:
    assert validate_tool_arguments(
        "band_get_memory", GetMemoryInput, {"memory_id": "ABC_123-x"}
    ) == {"memory_id": "ABC_123-x"}
