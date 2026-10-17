"""Load the runnable shared-resource example without starting a live agent."""

from __future__ import annotations

import runpy

from tests.paths import EXAMPLES_ROOT

EXAMPLE = runpy.run_path(str(EXAMPLES_ROOT / "anthropic" / "shared-resources.py"))
