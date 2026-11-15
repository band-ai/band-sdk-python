from __future__ import annotations

from importlib import import_module

import pytest

pytest.importorskip("crewai")


@pytest.fixture(scope="session", autouse=True)
def native_flow_storage_loaded() -> None:
    """Load Flow's native memory dependency before the lifecycle wait budgets."""
    import_module("lancedb")
