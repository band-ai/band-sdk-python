"""Meta-tests that detect config drift between source modules and registries.

If a new adapter or converter module is added to the codebase but not
registered in the conformance config builders (or the exclusion set),
these tests will fail — surfacing the gap before it silently reduces
coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.framework_configs.adapters import ADAPTER_CONFIGS, ADAPTER_EXCLUDED_MODULES
from tests.framework_configs.converters import (
    CONVERTER_CONFIGS,
    CONVERTER_EXCLUDED_MODULES,
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _discover_modules(package_dir: Path) -> set[str]:
    """Return base names (no .py) of public modules in *package_dir*.

    Skips ``__init__.py`` and any non-``.py`` files.
    """
    return {
        p.stem
        for p in package_dir.iterdir()
        if p.suffix == ".py" and p.name != "__init__.py"
    }


class TestAdapterConfigDrift:
    """Every adapter module must be registered or explicitly excluded."""

    def test_all_adapter_modules_are_covered(self):
        """Each module in src/adapters/ has a config or is excluded."""
        adapter_dir = _SRC_ROOT / "adapters"
        source_modules = _discover_modules(adapter_dir)
        registered_ids = {cfg.framework_id for cfg in ADAPTER_CONFIGS}
        covered = registered_ids | ADAPTER_EXCLUDED_MODULES
        uncovered = source_modules - covered

        assert not uncovered, (
            f"Adapter modules without conformance config or exclusion: {uncovered}. "
            f"Either add an AdapterConfig in tests/framework_configs/adapters.py "
            f"or add the module name to ADAPTER_EXCLUDED_MODULES."
        )

    def test_no_stale_exclusions(self):
        """Excluded adapter module names still exist on disk."""
        adapter_dir = _SRC_ROOT / "adapters"
        source_modules = _discover_modules(adapter_dir)
        stale = ADAPTER_EXCLUDED_MODULES - source_modules

        assert not stale, (
            f"ADAPTER_EXCLUDED_MODULES references modules that no longer exist: {stale}. "
            f"Remove them from the exclusion set."
        )

    def test_no_stale_configs(self):
        """Registered adapter framework_ids still correspond to a source module."""
        adapter_dir = _SRC_ROOT / "adapters"
        source_modules = _discover_modules(adapter_dir)
        registered_ids = {cfg.framework_id for cfg in ADAPTER_CONFIGS}

        # framework_id must match a module name (langgraph maps to langgraph.py, etc.)
        stale = registered_ids - source_modules
        assert not stale, (
            f"AdapterConfig framework_ids have no matching source module: {stale}. "
            f"Remove or update the config in tests/framework_configs/adapters.py."
        )


class TestConverterConfigDrift:
    """Every converter module must be registered or explicitly excluded."""

    def test_all_converter_modules_are_covered(self):
        """Each module in src/converters/ has a config or is excluded."""
        converter_dir = _SRC_ROOT / "converters"
        source_modules = _discover_modules(converter_dir)
        registered_ids = {cfg.framework_id for cfg in CONVERTER_CONFIGS}
        covered = registered_ids | CONVERTER_EXCLUDED_MODULES
        uncovered = source_modules - covered

        assert not uncovered, (
            f"Converter modules without conformance config or exclusion: {uncovered}. "
            f"Either add a ConverterConfig in tests/framework_configs/converters.py "
            f"or add the module name to CONVERTER_EXCLUDED_MODULES."
        )

    def test_no_stale_exclusions(self):
        """Excluded converter module names still exist on disk."""
        converter_dir = _SRC_ROOT / "converters"
        source_modules = _discover_modules(converter_dir)
        stale = CONVERTER_EXCLUDED_MODULES - source_modules

        assert not stale, (
            f"CONVERTER_EXCLUDED_MODULES references modules that no longer exist: {stale}. "
            f"Remove them from the exclusion set."
        )

    def test_no_stale_configs(self):
        """Registered converter framework_ids still correspond to a source module."""
        converter_dir = _SRC_ROOT / "converters"
        source_modules = _discover_modules(converter_dir)
        registered_ids = {cfg.framework_id for cfg in CONVERTER_CONFIGS}
        stale = registered_ids - source_modules
        assert not stale, (
            f"ConverterConfig framework_ids have no matching source module: {stale}. "
            f"Remove or update the config in tests/framework_configs/converters.py."
        )


class TestCrewAIConformanceGuards:
    """Verify that CrewAI conformance instances guard runtime methods.

    If the guard loop in ``_crewai_factory`` is accidentally removed, these
    tests will fail — preventing silent execution on MagicMock objects.
    """

    @staticmethod
    def _get_crewai_config():
        for cfg in ADAPTER_CONFIGS:
            if cfg.framework_id == "crewai":
                return cfg
        return None

    @pytest.mark.asyncio
    async def test_on_message_is_guarded(self):
        """on_message on a conformance instance must raise RuntimeError."""
        cfg = self._get_crewai_config()
        if cfg is None:
            pytest.skip("CrewAI adapter config not available")
        adapter = cfg.adapter_factory()
        with pytest.raises(RuntimeError, match="conformance instance"):
            await adapter.on_message()

    @pytest.mark.asyncio
    async def test_invoke_crew_is_guarded(self):
        """_invoke_crew on a conformance instance must raise RuntimeError."""
        cfg = self._get_crewai_config()
        if cfg is None:
            pytest.skip("CrewAI adapter config not available")
        adapter = cfg.adapter_factory()
        if not hasattr(adapter, "_invoke_crew"):
            pytest.skip("CrewAI adapter has no _invoke_crew method")
        with pytest.raises(RuntimeError, match="conformance instance"):
            await adapter._invoke_crew()
