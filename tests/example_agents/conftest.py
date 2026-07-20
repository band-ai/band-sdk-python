"""
Conftest for example tests.

Adds the examples directories to Python path so tests can import from them.
"""

from __future__ import annotations

import sys

from tests.paths import EXAMPLES_ROOT

sys.path.insert(0, str(EXAMPLES_ROOT / "langgraph"))
sys.path.insert(0, str(EXAMPLES_ROOT / "pydantic_ai"))
sys.path.insert(
    0, str(EXAMPLES_ROOT / "20-questions-arena")
)  # For 20 Questions Arena imports
