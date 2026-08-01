"""First-class ``ModelProvider`` implementations."""

from __future__ import annotations

import importlib
from typing import Any

from band.providers.types import (
    ANTHROPIC_PROVIDER_OPTION_KEYS,
    GEMINI_PROVIDER_OPTION_KEYS,
    AnthropicProviderOptions,
    GeminiProviderOptions,
)

# Each provider module imports its vendor SDK at module scope, so naming one
# here eagerly would make every provider's extra a hard dependency of all
# the others.
_LAZY_IMPORTS = {
    "AnthropicProvider": "anthropic",
    "GeminiProvider": "gemini",
}

__all__ = [
    "ANTHROPIC_PROVIDER_OPTION_KEYS",
    "GEMINI_PROVIDER_OPTION_KEYS",
    "AnthropicProvider",
    "AnthropicProviderOptions",
    "GeminiProvider",
    "GeminiProviderOptions",
]


def __getattr__(name: str) -> Any:
    """Lazy import providers to avoid loading optional dependencies."""
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{module_name}", __name__)
    return getattr(module, name)
