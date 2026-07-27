"""Infrastructure for optional public re-exports."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from typing import Any


def lazy_exports(
    package: str,
    **module_exports: Sequence[str],
) -> tuple[list[str], Callable[[str], Any]]:
    """Build a package's ``__all__`` and PEP 562 ``__getattr__``.

    Each keyword is a submodule of ``package`` and lists the exports it owns. A
    submodule is imported only when one of its names is first read, so importing
    the package never pulls in an optional extra::

        __all__, __getattr__ = lazy_exports(
            __name__,
            anthropic=["AnthropicAdapter"],
            codex=["CodexAdapter", "CodexAdapterConfig"],
        )
    """
    owners = {
        name: module for module, names in module_exports.items() for name in names
    }

    def resolve(name: str) -> Any:
        module = owners.get(name)
        if module is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        return getattr(importlib.import_module(f".{module}", package), name)

    return list(owners), resolve
