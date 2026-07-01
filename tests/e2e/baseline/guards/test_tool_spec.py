"""Guard for ``ToolSpec.as_callable`` signature synthesis (no live platform).

``as_callable`` synthesizes a real-signatured function from a Pydantic model so
pydantic-ai/agno can introspect the tool's arg schema. The defaults baked into
that signature must round-trip through the model exactly as if the field were
omitted — in particular a ``default_factory`` field must yield its produced value
(``[]``), not ``None``, which would otherwise fail the model's own validation the
first time the LLM omits the arg. These construct nothing live, so they run in any
lane.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from tests.e2e.baseline.toolkit.tools import ToolSpec


class _Input(BaseModel):
    """sample tool"""

    query: str  # required
    tags: list[str] = Field(default_factory=list)  # factory default
    limit: int = 5  # scalar default
    note: str | None = None  # None default


def _echo(args: _Input) -> str:
    return f"q={args.query} tags={args.tags} limit={args.limit} note={args.note}"


def test_omitted_optionals_use_model_defaults() -> None:
    """Calling with only the required arg applies every field's own default —
    crucially the ``default_factory`` field becomes ``[]`` rather than ``None``."""
    tool = ToolSpec(_Input, _echo).as_callable()
    assert tool(query="hi") == "q=hi tags=[] limit=5 note=None"


def test_supplied_values_are_forwarded() -> None:
    tool = ToolSpec(_Input, _echo).as_callable()
    assert tool(query="hi", tags=["a"], limit=2, note="x") == (
        "q=hi tags=['a'] limit=2 note=x"
    )


def test_factory_default_is_not_shared_across_calls() -> None:
    """A handler mutating the model's list must not leak into the next call's
    default (the model re-validates the forwarded default into a fresh list)."""

    def mutate(args: _Input) -> str:
        args.tags.append("X")
        return str(args.tags)

    tool = ToolSpec(_Input, mutate).as_callable()
    assert tool(query="a") == "['X']"
    assert tool(query="b") == "['X']"


def test_ctx_param_is_prepended_when_requested() -> None:
    tool = ToolSpec(_Input, _echo).as_callable(ctx_annotation=object)
    assert tool(None, query="hi") == "q=hi tags=[] limit=5 note=None"


class _OptionalBeforeRequired(BaseModel):
    """opt-before-required tool"""

    opt: int = 0  # optional declared first
    req: str  # required declared after


def test_optional_field_before_required_synthesizes_valid_signature() -> None:
    """Field order must not produce ``def f(opt=_d, req):`` — a defaulted param
    before a non-defaulted one is a SyntaxError. ``as_callable`` orders required
    params first regardless of the model's declaration order."""

    def echo(args: _OptionalBeforeRequired) -> str:
        return f"req={args.req} opt={args.opt}"

    tool = ToolSpec(_OptionalBeforeRequired, echo).as_callable()
    assert tool(req="hi") == "req=hi opt=0"
    # And with the pydantic-ai ctx param prepended.
    ctx_tool = ToolSpec(_OptionalBeforeRequired, echo).as_callable(
        ctx_annotation=object
    )
    assert ctx_tool(None, req="hi", opt=3) == "req=hi opt=3"


class _CtxField(BaseModel):
    """ctx-field tool"""

    ctx: str


def test_ctx_field_collision_raises_clear_error() -> None:
    """A model field named ``ctx`` would shadow the injected pydantic-ai context
    param; fail loud rather than emit a duplicate-argument SyntaxError."""
    spec = ToolSpec(_CtxField, lambda args: args.ctx)
    # agno path (no ctx injected) is fine — the field is a normal param.
    assert spec.as_callable()(ctx="hi") == "hi"
    # pydantic-ai path injects ``ctx`` → collision → clear error.
    with pytest.raises(ValueError, match="collides with the injected"):
        spec.as_callable(ctx_annotation=object)
