"""
Conftest for example tests.

Adds the examples directories to Python path so tests can import from them.
"""

import sys
from tests.paths import EXAMPLES_ROOT

# Add examples directories to path
examples_root = EXAMPLES_ROOT
sys.path.insert(0, str(examples_root / "langgraph"))
sys.path.insert(0, str(examples_root / "pydantic_ai"))
sys.path.insert(
    0, str(examples_root / "20-questions-arena")
)  # For 20 Questions Arena imports
