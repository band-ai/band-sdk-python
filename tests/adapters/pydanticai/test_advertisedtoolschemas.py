"""AdvertisedToolSchemas behavior for Pydantic AI."""

from __future__ import annotations

import json

import pytest

from band.core.tool_filter import sanitize_tool_schema
from band.core.types import Capability
from band.runtime.tools import (
    TASK_TOOL_NAMES,
    BandTool,
    get_tool_description,
    platform_args_schema,
)
from tests.adapters.pydanticai.kit import _started_adapter
from tests.framework_configs.adapters import pydantic_ai_probe_tools


class TestAdvertisedToolSchemas:
    """Per-argument text fidelity is asserted in test_tool_text_drift.

    What is pydantic-ai-specific, and checked here, is that the master model's
    own description and provider-safe JSON schema reach the framework — nothing
    is re-derived from a hand-written function signature any more.
    """

    @pytest.mark.asyncio
    async def test_tool_blurb_is_the_master_docstring(self):
        blurbs = {
            name: schema.description
            for name, schema in (await pydantic_ai_probe_tools()).items()
        }

        assert blurbs, "no tools registered, so nothing was actually checked"
        assert blurbs == {name: get_tool_description(name).strip() for name in blurbs}

    @pytest.mark.asyncio
    async def test_advertised_schema_is_the_master_schema(self):
        schemas = {
            name: schema.json_schema
            for name, schema in (await pydantic_ai_probe_tools()).items()
        }

        assert schemas, "no tools registered, so nothing was actually checked"
        assert schemas == {
            name: sanitize_tool_schema(
                platform_args_schema(name).model_json_schema(),
                drop_numeric_bounds=True,
            )
            for name in schemas
        }

    @pytest.mark.asyncio
    async def test_task_schemas_avoid_single_value_const(self):
        """Task tools are the ones with a single-value ``Literal``.

        Pydantic renders that as ``const``, which some providers' restricted
        JSON-Schema subsets reject; ``platform_args_schema`` normalizes it to a
        one-member ``enum``, and this is the proof the sanitized schema — not a
        raw ``model_json_schema()`` — is what gets advertised.
        """
        adapter = await _started_adapter(capabilities=Capability.TASKS)
        tools = adapter._agent._function_toolset.tools

        for name in TASK_TOOL_NAMES:
            assert "const" not in json.dumps(tools[name].function_schema.json_schema)

        include = tools[BandTool.GET_TASK].function_schema.json_schema["properties"][
            "include"
        ]
        assert {"enum": ["history"], "type": "string"} in include["anyOf"]
