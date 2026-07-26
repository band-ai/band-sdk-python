"""Infrastructure for optional public re-exports."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def lazy_exports(
    package_name: str,
    module_exports: Mapping[str, Sequence[str]],
) -> tuple[tuple[str, ...], Callable[[str], Any], Callable[[], list[str]]]:
    """Create a lazy package export surface from module-owned names."""
    export_modules: dict[str, str] = {}
    exports: list[str] = []
    for module_name, names in module_exports.items():
        for name in names:
            if name in export_modules:
                raise ValueError(f"duplicate lazy export {name!r} in {package_name!r}")
            export_modules[name] = module_name
            exports.append(name)

    def resolve(name: str) -> Any:
        try:
            module_name = export_modules[name]
        except KeyError as error:
            raise AttributeError(
                f"module {package_name!r} has no attribute {name!r}"
            ) from error

        module = importlib.import_module(f".{module_name}", package_name)
        value = getattr(module, name)
        setattr(sys.modules[package_name], name, value)
        return value

    def exported_names() -> list[str]:
        return sorted(set(sys.modules[package_name].__dict__) | set(exports))

    return tuple(exports), resolve, exported_names
