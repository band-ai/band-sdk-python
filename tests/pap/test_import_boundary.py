"""Keep PAP independent of agent frameworks and SDK implementation modules."""

from __future__ import annotations

import pytest

from tests.pap.imports import forbidden_imports
from tests.paths import BAND_PAP_DIR


class TestImportBoundary:
    def test_imports_only_declared_libraries_and_public_band_interfaces(self) -> None:
        violations = [
            f"{source.name}: {violation}"
            for source in (BAND_PAP_DIR / "src").rglob("*.py")
            for violation in forbidden_imports(source.read_text())
        ]
        assert not violations, "Undeclared dependency or SDK internal: " + ", ".join(
            violations
        )

    @pytest.mark.parametrize(
        "source",
        [
            "from band import runtime",
            "from band import runtime as public",
            "from band.core import delivery",
            "from band.core.simple_adapter import report_unsettled_turn",
            "from band import *",
            "import band",
            "import band.runtime.execution",
            "from nooa import Agent",
            "from . import runtime",
        ],
    )
    def test_guard_rejects_parent_import_and_alias_bypasses(self, source: str) -> None:
        assert forbidden_imports(source)

    @pytest.mark.parametrize(
        "source",
        [
            "from band import Agent",
            "from band.core.simple_adapter import SimpleAdapter as Adapter",
            "from band.core.types import PlatformMessage, FeatureKwargs",
            "from band.client.rest import AsyncRestClient",
            "from bandpap.discovery import DiscoveryDocument",
        ],
    )
    def test_guard_accepts_reviewed_public_contracts(self, source: str) -> None:
        assert forbidden_imports(source) == []
