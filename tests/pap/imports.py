"""Static import contract for the independently packaged PAP integration."""

from __future__ import annotations

import ast

LIBRARY_ROOTS = frozenset(
    {
        "__future__",
        "asyncio",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "hmac",
        "json",
        "logging",
        "pathlib",
        "re",
        "secrets",
        "sqlite3",
        "typing",
        "urllib",
        "uuid",
        "bandpap",
        "pydantic",
    }
)
BAND_SYMBOLS = {
    "band": frozenset({"Agent"}),
    "band.core.simple_adapter": frozenset({"SimpleAdapter"}),
    "band.core.adapterconfig": frozenset({"BaseAdapterConfig"}),
    "band.core.protocols": frozenset({"AgentToolsProtocol"}),
    "band.core.types": frozenset({"AgentInput", "FeatureKwargs", "PlatformMessage"}),
    "band.client.rest": frozenset({"AsyncRestClient"}),
}


def forbidden_imports(source: str) -> list[str]:
    violations = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.Import(names=names):
                violations.extend(
                    f"import {name.name}"
                    for name in names
                    if name.name.split(".")[0] not in LIBRARY_ROOTS
                )
            case ast.ImportFrom(module=module, names=names, level=level):
                if level or module is None:
                    violations.append("relative import")
                elif module.split(".")[0] == "band":
                    violations.extend(
                        f"from {module} import {name.name}"
                        for name in names
                        if name.name not in BAND_SYMBOLS.get(module, frozenset())
                    )
                elif module.split(".")[0] not in LIBRARY_ROOTS:
                    violations.append(f"from {module}")
    return violations
