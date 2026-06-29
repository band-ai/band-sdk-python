"""Discovery-guard + registry smokes (no live platform, no construction).

These prove the registry is self-consistent and that a newly-added adapter cannot
be silently skipped: the folder scan over ``src/band/adapters/`` (minus the
documented DENY bridges) must equal the registered set exactly. They construct
nothing, so they run in any lane.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.requires import Dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.adapters import (
    DENY,
    assert_registry_covers_discovered,
    build_adapter,
    discovered_agent_ids,
    registered_ids,
    specs,
)


def test_registry_covers_discovered_adapters() -> None:
    """Every non-bridge adapter under src/band/adapters/ is registered (and vice
    versa). This is the loud failure that forces a new adapter to be wired up."""
    assert_registry_covers_discovered()


def test_deny_list_is_disjoint_from_registry() -> None:
    """The excluded bridges/parlant are never registered as matrix adapters."""
    assert DENY.isdisjoint(registered_ids())
    assert DENY.isdisjoint(discovered_agent_ids())


def test_build_adapter_rejects_unknown_id(
    baseline_settings: BaselineSettings,
) -> None:
    """An unregistered id is a programming error and names the registered set."""
    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter("does_not_exist", baseline_settings)


def test_every_spec_requires_dep_members() -> None:
    """Requirements are typed ``Dep`` members (guards against stray strings)."""
    for spec in specs():
        assert spec.requires, f"{spec.id} declares no requirements"
        assert all(isinstance(dep, Dep) for dep in spec.requires), spec.id


def test_supports_filter_selects_memory_adapters() -> None:
    """The capability filter narrows the matrix (the 'memory matrix' use case)."""
    from band.core.types import Capability

    memory_ids = {spec.id for spec in specs(supports={Capability.MEMORY})}
    assert memory_ids <= registered_ids()
    assert "anthropic" in memory_ids  # a known memory-tool-loop adapter
