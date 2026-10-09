"""How a Band platform tool function becomes a Parlant tool.

Each tool is declared once at module level with ``@band_tool``, which only
records a ``BandToolSpec``. ``build_band_tool`` builds a fresh Parlant entry
per call, so master-model text edits keep propagating and the declared
function itself is never mutated.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, get_args, get_origin

import parlant.sdk as p
from parlant.core.tools import ToolParameterOptions, ToolResult

from band.integrations.parlant.guard import guard_failures
from band.runtime.tools import get_tool_description, resolve_tool_model


@dataclass(frozen=True)
class BandToolSpec:
    """A Band tool function plus how its Parlant entry is described and guarded."""

    func: Callable[..., Any]
    failure: str
    extra_doc: str = ""
    param_notes: Mapping[str, str] = field(default_factory=dict)
    mention_hints: bool = False

    @property
    def name(self) -> str:
        return self.func.__name__


def band_tool(
    failure: str,
    *,
    extra_doc: str = "",
    param_notes: Mapping[str, str] | None = None,
    mention_hints: bool = False,
) -> Callable[[Callable[..., Any]], BandToolSpec]:
    """Declare a Band tool function's spec; ``build_band_tool`` builds its entry.

    ``extra_doc`` and ``param_notes`` only append to the master model's text,
    so a master-model edit keeps propagating.
    """

    def declare(func: Callable[..., Any]) -> BandToolSpec:
        return BandToolSpec(
            func=func,
            failure=failure,
            extra_doc=extra_doc,
            param_notes=dict(param_notes or {}),
            mention_hints=mention_hints,
        )

    return declare


def build_band_tool(spec: BandToolSpec) -> Any:
    """A guarded Parlant ``ToolEntry`` described from the master model."""
    wrapper = guard_failures(
        spec.func, failure=spec.failure, mention_hints=spec.mention_hints
    )
    wrapper.__signature__ = _described_signature(spec)  # type: ignore[attr-defined]
    wrapper.__doc__ = get_tool_description(spec.name).rstrip() + spec.extra_doc
    return p.tool(wrapper)


def or_none(value: str) -> str | None:
    """``""`` is how a Parlant model omits a string; the platform wants ``None``."""
    return value or None


def invalid_choice(name: str, value: str, choices: Iterable[str]) -> ToolResult:
    """The model-visible error for a *value* outside a closed vocabulary."""
    quoted = ", ".join(f"'{choice}'" for choice in choices)
    return ToolResult(data=f"Error: Invalid {name} '{value}'. Use one of {quoted}")


def _described_signature(spec: BandToolSpec) -> inspect.Signature:
    """*spec*'s signature with each argument's master-model description attached.

    Parlant's schema builder never reads a docstring's ``Args:`` section — a
    parameter is described only via
    ``Annotated[T, ToolParameterOptions(description=...)]``.
    """
    signature = inspect.signature(spec.func, eval_str=True)
    model = resolve_tool_model(spec.name)
    if model is None:
        return signature
    return signature.replace(
        parameters=[
            _described_parameter(param, model=model, notes=spec.param_notes)
            for param in signature.parameters.values()
        ]
    )


def _described_parameter(
    param: inspect.Parameter, *, model: Any, notes: Mapping[str, str]
) -> inspect.Parameter:
    """*param* annotated with its master field's description, if it has one."""
    master_field = model.model_fields.get(param.name)
    if param.name == "context" or master_field is None or not master_field.description:
        return param
    description = master_field.description
    if choices := _literal_choices(master_field.annotation):
        description = description.rstrip() + f" One of: {', '.join(choices)}."
    if note := notes.get(param.name):
        description = description.rstrip() + note
    return param.replace(
        annotation=Annotated[
            param.annotation, ToolParameterOptions(description=description)
        ]
    )


def _literal_choices(annotation: Any) -> tuple[str, ...] | None:
    """String choices of a master field's ``Literal[...]`` annotation, if any.

    Parlant's schema builder turns a real ``enum.Enum`` class into a JSON
    Schema ``enum``, but a bare ``Literal[...]`` isn't one — it falls into
    the builder's list-only generic-container branch and raises. So a
    Literal-typed master field can't be passed through as the parameter's own
    annotation; its choices are folded into the description as prose instead.
    """
    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        if args and all(isinstance(a, str) for a in args):
            return args
    return None
