"""Native async tool signatures preserve model defaults and context boundaries."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.toolkit.tools import ToolSpec
from tests.framework_conformance.toolspec import (
    ContextField,
    OptionalBeforeRequired,
    SampleInput,
    echo,
)


class TestToolSpec:
    async def test_omitted_optionals_use_model_defaults(self) -> None:
        tool = ToolSpec(SampleInput, echo).as_callable()
        assert await tool(query="hi") == "q=hi tags=[] limit=5 note=None"

    async def test_supplied_values_are_forwarded(self) -> None:
        tool = ToolSpec(SampleInput, echo).as_callable()
        assert await tool(query="hi", tags=["a"], limit=2, note="x") == (
            "q=hi tags=['a'] limit=2 note=x"
        )

    async def test_factory_default_is_not_shared_across_calls(self) -> None:
        def mutate(args: SampleInput) -> str:
            args.tags.append("X")
            return str(args.tags)

        tool = ToolSpec(SampleInput, mutate).as_callable()
        assert await tool(query="a") == "['X']"
        assert await tool(query="b") == "['X']"

    async def test_ctx_param_is_prepended_when_requested(self) -> None:
        tool = ToolSpec(SampleInput, echo).as_callable(ctx_annotation=object)
        assert await tool(None, query="hi") == "q=hi tags=[] limit=5 note=None"

    async def test_optional_field_before_required_synthesizes_valid_signature(
        self,
    ) -> None:
        def echo_fields(args: OptionalBeforeRequired) -> str:
            return f"req={args.req} opt={args.opt}"

        spec = ToolSpec(OptionalBeforeRequired, echo_fields)
        assert await spec.as_callable()(req="hi") == "req=hi opt=0"
        assert (
            await spec.as_callable(ctx_annotation=object)(None, req="hi", opt=3)
            == "req=hi opt=3"
        )

    async def test_ctx_field_collision_raises_clear_error(self) -> None:
        spec = ToolSpec(ContextField, lambda args: args.ctx)
        assert await spec.as_callable()(ctx="hi") == "hi"
        with pytest.raises(ValueError, match="collides with the injected"):
            spec.as_callable(ctx_annotation=object)
