"""Shared tool-schema filtering helper.

Single source of truth for applying include/exclude/category filters
from AdapterFeatures to tool schema lists.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar

from band.core.types import AdapterFeatures

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Numeric-range JSON-Schema keywords. Some providers reject these on integer or
# number parameters in tool/function schemas (e.g. Gemini, and Anthropic-backed
# Agno: "For 'integer' type, property 'maximum' is not supported").
_NUMERIC_BOUND_KEYWORDS = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
)

# JSON-Schema keywords whose values are maps of *arbitrary property names* to
# subschemas. Their child keys are names, not keywords, so they must never be
# stripped (a tool param literally named ``maximum`` must survive).
_NAME_MAP_KEYWORDS = frozenset(
    {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
)


def sanitize_tool_schema(
    schema: Any,
    *,
    drop_numeric_bounds: bool = False,
    drop_additional_properties: bool = False,
) -> Any:
    """Recursively remove JSON-Schema keywords that some providers reject.

    Returns a new structure; the input is left untouched. Centralizes the
    schema scrubbing that model adapters need before handing Band tool schemas
    to a provider that rejects otherwise-valid JSON Schema.

    Args:
        schema: A JSON-Schema dict (or any nested fragment of one).
        drop_numeric_bounds: Drop ``minimum``/``maximum``/``exclusiveMinimum``/
            ``exclusiveMaximum``/``multipleOf``. The bounds remain enforced
            wherever tool-call arguments are validated against the source model.
        drop_additional_properties: Drop ``additionalProperties`` (rejected by
            Gemini).

    Keys are stripped only where they act as schema keywords, never where they
    are property names under ``properties``/``$defs``/etc.
    """
    drop: set[str] = set()
    if drop_numeric_bounds:
        drop |= _NUMERIC_BOUND_KEYWORDS
    if drop_additional_properties:
        drop.add("additionalProperties")
    return _sanitize(schema, drop)


def _sanitize(node: Any, drop: frozenset[str] | set[str]) -> Any:
    if isinstance(node, list):
        return [_sanitize(item, drop) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key in drop:
            continue
        if key in _NAME_MAP_KEYWORDS and isinstance(value, dict):
            # Values here are name -> subschema; keep names, scrub each subschema.
            cleaned[key] = {
                name: _sanitize(subschema, drop) for name, subschema in value.items()
            }
        else:
            cleaned[key] = _sanitize(value, drop)
    return cleaned


def filter_tool_schemas(
    schemas: list[T],
    features: AdapterFeatures,
    *,
    get_name: Callable[[T], str],
    get_category: Callable[[T], str | None] | None = None,
) -> list[T]:
    """Apply include/exclude/category filters from AdapterFeatures.

    Filters are applied in strict precedence order:

    1. **include_categories** — keep only schemas whose category is in the set.
    2. **include_tools** — keep only schemas whose name is in the set.
    3. **exclude_tools** — drop schemas whose name is in the set.

    Each stage narrows the result of the previous one, so
    ``include_categories=["chat"]`` + ``include_tools=["band_store_memory"]``
    yields an empty list when ``band_store_memory`` is not in the ``"chat"``
    category.

    Args:
        schemas: List of tool schemas (any type).
        features: AdapterFeatures with filtering config.
        get_name: Extracts the tool name from a schema object.
        get_category: Extracts the category from a schema object.
            If None, include_categories filtering is skipped with a warning.

    Returns:
        Filtered list of schemas.
    """
    available_names = {get_name(s) for s in schemas}
    result = list(schemas)

    if features.include_categories is not None:
        if get_category is None:
            logger.warning(
                "include_categories is set but this adapter does not support "
                "category filtering (ignored): %s",
                features.include_categories,
            )
        else:
            cats = set(features.include_categories)
            result = [s for s in result if get_category(s) in cats]

    if features.include_tools is not None:
        names = set(features.include_tools)
        unmatched = names - available_names
        if unmatched:
            logger.warning(
                "include_tools contains unknown names: %s",
                ", ".join(sorted(unmatched)),
            )
        result = [s for s in result if get_name(s) in names]

    if features.exclude_tools is not None:
        names = set(features.exclude_tools)
        unmatched = names - available_names
        if unmatched:
            logger.warning(
                "exclude_tools contains unknown names: %s",
                ", ".join(sorted(unmatched)),
            )
        result = [s for s in result if get_name(s) not in names]

    return result
