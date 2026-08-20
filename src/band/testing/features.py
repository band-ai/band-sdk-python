"""Test helper for the flattened adapter feature-kwarg construction surface."""

from __future__ import annotations

from typing import Any

from band.core.types import AdapterFeatures


def feature_kwargs(features: AdapterFeatures | None) -> dict[str, Any]:
    """Unpack an ``AdapterFeatures`` into the flattened constructor kwargs.

    Every adapter takes ``emit=``/``capabilities=``/... directly, not a
    wrapping ``features=AdapterFeatures(...)`` object. ``None`` spreads to
    nothing, so the adapter's own default (everything it supports) applies.
    """
    if features is None:
        return {}
    return {
        "emit": features.emit,
        "capabilities": features.capabilities,
        "include_tools": features.include_tools,
        "exclude_tools": features.exclude_tools,
        "include_categories": features.include_categories,
    }
