"""Guards for the baseline adapter support registry."""

from __future__ import annotations

import pytest

from tests.baseline.registry import (
    AdapterSupport,
    SUPPORT,
    assert_support_is_complete,
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
