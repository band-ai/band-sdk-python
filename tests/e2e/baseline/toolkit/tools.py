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

    def _build_signature(self, ctx_annotation: Any) -> tuple[list[str], dict[str, Any]]:
        """The parameter-string list and the matching ``exec`` namespace.

        Each non-required field's default value is baked into the namespace as
        ``_default_<f>`` and referenced by the param string, so the two are
        returned together. ``call_default_factory=True`` so a ``default_factory``
        field yields its produced value (e.g. ``[]``), not ``None`` — otherwise
        we'd bake ``None`` into the signature and fail the model's validation when
        the arg is omitted.
        """
        ns: dict[str, Any] = {"_handler": self.handler, "_model": self.model}
        params: list[str] = ["ctx"] if ctx_annotation is not None else []
        for fname, field in self.model.model_fields.items():
            if field.is_required():
                params.append(fname)
            else:
                ns[f"_default_{fname}"] = field.get_default(call_default_factory=True)
                params.append(f"{fname}=_default_{fname}")
        return params, ns

    def _annotations(self, ctx_annotation: Any) -> dict[str, Any]:
        """The ``__annotations__`` map: optional ``ctx``, each field, ``return``."""
        annotations: dict[str, Any] = (
            {"ctx": ctx_annotation} if ctx_annotation is not None else {}
        )
        for fname, field in self.model.model_fields.items():
            annotations[fname] = (
                field.annotation if field.annotation is not None else Any
            )
        annotations["return"] = str
        return annotations

    def as_callable(self, *, ctx_annotation: Any = None) -> Callable[..., str]:
        """A native tool function with a real signature built from the model fields.

        For frameworks that take plain callables (pydantic-ai, agno). When
        ``ctx_annotation`` is given a leading ``ctx`` parameter is prepended with
        that annotation — pydantic-ai needs ``RunContext`` to recognise context;
        agno passes ``None``. The function validates its kwargs through the model
        and delegates to ``handler``, so behaviour matches the ``CustomToolDef`` path.

        The parameters must be *real* named params (not ``*args/**kwargs``): agno
        reads ``inspect.signature`` and pydantic-ai reads the actual code object, so
        both derive the tool's arg schema from a synthesized-then-``exec``'d def.
        """
        params, ns = self._build_signature(ctx_annotation)
        construct = ", ".join(f"{f}={f}" for f in self.model.model_fields)
        src = f"def {self.name}({', '.join(params)}):\n    return _handler(_model({construct}))\n"
        exec(src, ns)  # noqa: S102 - synthesize a real signature the frameworks introspect
        tool: Callable[..., str] = ns[self.name]
        tool.__annotations__ = self._annotations(ctx_annotation)
        tool.__doc__ = self.description
        return tool
