"""Adapter package export surface stays aligned with lazy imports."""

from __future__ import annotations

from band.adapters import _LAZY_IMPORTS, __all__


def test_all_matches_lazy_imports() -> None:
    assert set(__all__) == set(_LAZY_IMPORTS)
    assert list(__all__) == list(_LAZY_IMPORTS)
