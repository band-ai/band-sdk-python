"""Load the draft example without starting a platform agent."""

from __future__ import annotations

import runpy

import pytest

from tests.paths import EXAMPLES_ROOT

pytest.importorskip("pydantic_ai")

EXAMPLE = runpy.run_path(str(EXAMPLES_ROOT / "pydantic_ai" / "draft-workspace.py"))
