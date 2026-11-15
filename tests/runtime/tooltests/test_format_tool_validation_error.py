from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from band.runtime.tools import format_tool_validation_error


class TestFormatToolValidationError:
    """Pins the exact wire-message shape `format_tool_validation_error` produces.

    The published band-mcp CLI surfaces this string verbatim to its callers
    (via ``StandaloneResolver``), so the separator between multiple field
    errors and the dotted-path format for a nested field are part of the
    wire contract, not just an internal formatting detail free to drift.
    """

    def test_multiple_and_nested_field_errors_joined_with_comma(self) -> None:
        class Nested(BaseModel):
            name: str

        class Model(BaseModel):
            items: list[Nested]
            extra: str

        with pytest.raises(ValidationError) as exc_info:
            Model(items=[{}], extra=None)

        message = format_tool_validation_error("some_tool", exc_info.value)

        assert message == (
            "Invalid arguments for some_tool: "
            "items.0.name: Field required, "
            "extra: Input should be a valid string"
        )
