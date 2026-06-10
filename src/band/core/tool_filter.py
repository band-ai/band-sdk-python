"""Shared tool-schema filtering helper.

Single source of truth for applying include/exclude/category filters
from AdapterFeatures to tool schema lists.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from band.core.types import AdapterFeatures

logger = logging.getLogger(__name__)

T = TypeVar("T")


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
