"""Keep the protocol integration independent of agent runtimes and SDK internals."""

from __future__ import annotations

import ast

from tests.paths import BAND_PAP_DIR


class TestImportBoundary:
    def test_imports_only_declared_libraries_and_public_band_interfaces(self) -> None:
        allowed_roots = {
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
        allowed_band_modules = {
            "band",
            "band.core.simple_adapter",
            "band.core.adapterconfig",
            "band.core.protocols",
            "band.core.types",
            "band.runtime.types",
            "band.runtime.features",
            "band.client.rest",
            "band.client.streaming",
        }
        violations = []
        for source in (BAND_PAP_DIR / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                modules = []
                match node:
                    case ast.Import(names=names):
                        modules = [name.name for name in names]
                    case ast.ImportFrom(module=module) if module:
                        modules = [module]
                for module in modules:
                    if (
                        module not in allowed_band_modules
                        and module.split(".")[0] not in allowed_roots
                    ):
                        violations.append(f"{source.name}:{node.lineno}: {module}")
        assert not violations, "Undeclared dependency or SDK internal: " + ", ".join(
            violations
        )
