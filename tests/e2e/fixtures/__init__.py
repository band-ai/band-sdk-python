"""E2E fixture plugins, loaded via ``pytest_plugins`` in ``tests/e2e/conftest.py``.

Split by concern: ``clients`` (config + REST/WS clients), ``rooms`` (room
allocation + agent identity), ``memory`` (memory-test toolkit).
"""

from __future__ import annotations
