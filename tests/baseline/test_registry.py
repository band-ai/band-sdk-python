"""Guards for the baseline adapter support registry."""

from __future__ import annotations

import pytest

from tests.baseline.registry import (
    Adapter,
    AdapterSupport,
    SUPPORT,
    assert_support_is_complete,
    support_for,
)


def test_every_adapter_declares_a_baseline_path_or_reason() -> None:
    assert_support_is_complete()


@pytest.mark.parametrize("support", SUPPORT, ids=lambda item: item.adapter.value)
def test_adapter_is_supported_or_explicitly_not_applicable(
    support: AdapterSupport,
) -> None:
    """Unsupported adapters stay visible as pytest skips with a concrete reason."""
    if support.supported:
        assert support.injection
    else:
        pytest.skip(support.reason)


def test_anthropic_is_the_first_registered_injection_path() -> None:
    assert support_for(Adapter.ANTHROPIC).supported
    assert len([item for item in SUPPORT if item.supported]) == 1
