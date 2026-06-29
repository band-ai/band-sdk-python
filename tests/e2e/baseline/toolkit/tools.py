"""Framework-agnostic custom-tool spec for the matrix.

Define a custom tool **once** as a ``ToolSpec`` (an input model + a handler); the
adapter builders translate it to whatever the framework needs — band
``CustomToolDef`` for the tool-loop adapters, or a native callable for pydantic-ai
and agno. So a test passes the *same* tool to ``@with_agents`` / ``@across_adapters``
regardless of adapter, instead of hand-writing a different tool per framework.

The tool *name* (used to register it and asserted via ``ToolCalls.assert_fired``) is
derived from the model, so prompts and assertions can't drift.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name


@dataclass(frozen=True)
class ToolSpec:
    """A custom tool: a Pydantic input ``model`` plus a ``handler(model) -> str``."""

    model: type[BaseModel]
    handler: Callable[[BaseModel], str]

    @property
    def name(self) -> str:
        """Stable tool name derived from the model (matches ``CustomToolDef``)."""
        return get_custom_tool_name(self.model)

    @property
    def description(self) -> str:
        return (self.model.__doc__ or self.name).strip()

    def as_custom_tool_def(self) -> CustomToolDef:
        """band ``CustomToolDef`` form, for the tool-loop adapters (anthropic, …)."""
        return (self.model, self.handler)

    def as_callable(self, *, ctx_annotation: Any = None) -> Callable[..., str]:
        """A native tool function with a real signature built from the model fields.

        For frameworks that take plain callables (pydantic-ai, agno). When
        ``ctx_annotation`` is given a leading ``ctx`` parameter is prepended with
        that annotation — pydantic-ai needs ``RunContext`` to recognise context;
        agno passes ``None``. The function validates its kwargs through the model
        and delegates to ``handler``, so behaviour matches the ``CustomToolDef`` path.
        """
        model = self.model
        fields = model.model_fields
        # Build a function with *real* named parameters (not *args/**kwargs): agno
        # reads inspect.signature, pydantic-ai reads the actual code object, so the
        # parameters must be real for both to derive the tool's arg schema.
        ns: dict[str, Any] = {"_handler": self.handler, "_model": model}
        sig: list[str] = ["ctx"] if ctx_annotation is not None else []
        for fname, field in fields.items():
            if field.is_required():
                sig.append(fname)
            else:
                ns[f"_default_{fname}"] = field.get_default()
                sig.append(f"{fname}=_default_{fname}")
        construct = ", ".join(f"{fname}={fname}" for fname in fields)
        src = f"def {self.name}({', '.join(sig)}):\n    return _handler(_model({construct}))\n"
        exec(src, ns)  # noqa: S102 - synthesize a real signature the frameworks introspect
        tool: Callable[..., str] = ns[self.name]

        annotations: dict[str, Any] = (
            {"ctx": ctx_annotation} if ctx_annotation is not None else {}
        )
        for fname, field in fields.items():
            annotations[fname] = field.annotation if field.annotation is not None else Any
        annotations["return"] = str
        tool.__annotations__ = annotations
        tool.__doc__ = self.description
        return tool
