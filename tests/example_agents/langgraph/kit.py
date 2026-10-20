"""Load the runnable database example without starting a platform agent."""

from __future__ import annotations

import runpy

import pytest

from tests.paths import EXAMPLES_ROOT

pytest.importorskip("langchain_openai")
pytest.importorskip("aiosqlite")

EXAMPLE = runpy.run_path(str(EXAMPLES_ROOT / "langgraph" / "shared-database.py"))
