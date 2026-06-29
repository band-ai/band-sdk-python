"""Baseline pytest fixtures, grouped by concern and re-exported from conftest.

These modules are *not* registered via ``pytest_plugins`` (deprecated in a
non-root conftest); instead ``conftest.py`` imports their fixtures so pytest
registers them, scoped to the baseline subtree. Cross-module fixture
dependencies resolve by name.
"""

from __future__ import annotations
