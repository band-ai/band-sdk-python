"""StoreMemoryArgsSchema behavior at the CrewAI tool boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.core.memory_types import (
    memory_type_field_description,
)


class TestStoreMemoryArgsSchema:
    """CrewAI advertises the master model, so master text and validators apply."""

    def test_type_description_comes_from_master(self, platform_args_schemas) -> None:

        schema = platform_args_schemas["band_store_memory"]
        assert (
            schema.model_fields["type"].description == memory_type_field_description()
        )

    def test_rejects_subject_scope_without_subject_id(
        self, platform_args_schemas
    ) -> None:

        with pytest.raises(ValidationError, match="requires a subject_id"):
            platform_args_schemas["band_store_memory"].model_validate(
                {
                    "content": "remember this",
                    "system": "working",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "subject",
                }
            )

    def test_rejects_type_for_wrong_system(self, platform_args_schemas) -> None:

        with pytest.raises(
            ValidationError, match="type `semantic` is not valid for system `sensory`"
        ):
            platform_args_schemas["band_store_memory"].model_validate(
                {
                    "content": "remember this",
                    "system": "sensory",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "organization",
                }
            )
