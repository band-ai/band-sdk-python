"""Shared E2E constants with no dependencies.

Kept import-side-effect free (no dotenv/pytest) so both the e2e ``E2ESettings``
and the standalone baseline ``BaselineSettings`` can single-source defaults
without coupling to each other's module-load behavior.
"""

from __future__ import annotations

# Default per-turn response budget (seconds) for live E2E tests, overridable via
# the E2E_TIMEOUT env var. Single-sourced here because two independent settings
# classes expose the same E2E_TIMEOUT knob and previously drifted (30 vs 60).
DEFAULT_E2E_TIMEOUT_S = 120
