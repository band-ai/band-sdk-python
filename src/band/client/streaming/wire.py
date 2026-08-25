from __future__ import annotations

from types import NoneType, UnionType
from typing import Any, Self, TypeVar, Union, get_args, get_origin

import band_sdk_core
from pydantic import BaseModel, ConfigDict

try:
    from opentelemetry import propagate
except ImportError:
    propagate = None

ModelT = TypeVar("ModelT", bound=BaseModel)


def current_traceparent() -> str | None:
    """The active W3C traceparent, or ``None`` when OpenTelemetry isn't installed."""
    if propagate is None:
        return None
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


class WirePayload(BaseModel):
    """Base for event payload models validated on the wire by band-sdk-core.

    Field rules and normalization live in band-sdk-core, not here -- these
    models are rule-free typed projections. ``from_wire`` is the wire path:
    it runs band-sdk-core's validation, then hydrates without re-validating.
    The plain constructor stays available for internal, non-wire construction
    (e.g. synthesizing a replay event from already-trusted data).
    """

    model_config = ConfigDict(extra="allow")

    @classmethod
    def from_wire(cls, event: str, payload: dict[str, Any]) -> Self:
        normalized = band_sdk_core.validate_event_payload(
            event, payload, current_traceparent()
        )
        return _hydrate(cls, normalized)


def _hydrate(model_cls: type[ModelT], data: dict[str, Any]) -> ModelT:
    """Recursively construct ``model_cls`` from already-validated wire data.

    ``model_construct`` alone only fills the outermost model, leaving nested
    models and lists of models as plain dicts -- this walks the model's own
    field annotations to construct those too. Nothing here re-validates:
    band-sdk-core has already decided the shape is valid.
    """
    fields = model_cls.model_fields
    known_keys = {field.alias or name for name, field in fields.items()}
    known_values = {
        name: _hydrate_value(field.annotation, data[field.alias or name])
        for name, field in fields.items()
        if (field.alias or name) in data
    }
    extra_values = {k: v for k, v in data.items() if k not in known_keys}
    return model_cls.model_construct(**known_values, **extra_values)


def _unwrap_optional(annotation: Any) -> Any:
    """The ``X`` in an ``X | None`` annotation, unchanged otherwise."""
    if get_origin(annotation) not in (Union, UnionType):
        return annotation
    members = [arg for arg in get_args(annotation) if arg is not NoneType]
    return members[0] if len(members) == 1 else annotation


def _hydrate_value(annotation: Any, value: Any) -> Any:
    """Hydrate one field's value per its declared type: a list constructs
    each element, a nested model constructs recursively, anything else
    (scalars, dicts with no model behind them) passes through untouched.
    """
    if value is None:
        return None

    annotation = _unwrap_optional(annotation)
    match get_origin(annotation):
        case origin if origin is list:
            (item_type,) = get_args(annotation)
            return [_hydrate_value(item_type, item) for item in value]
        case None if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return _hydrate(annotation, value)
        case _:
            return value
