"""Adapter turn helpers (re-exported from ``oneshot``).

``SimpleAdapterBackend`` was removed: Agent and ``run_oneshot_turn`` call the
adapter directly. Import ``run_adapter_turn`` / ``run_oneshot_turn`` from
``band.core.backends.oneshot``.
"""

from __future__ import annotations

from band.core.backends.oneshot import (
    execute_turn,
    run_adapter_turn,
    run_oneshot_turn,
)

__all__ = [
    "execute_turn",
    "run_adapter_turn",
    "run_oneshot_turn",
]
