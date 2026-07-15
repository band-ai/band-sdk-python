"""
Locate `examples/sandbox/staging-smoke/` from any script under `skill/scripts/`
and put it on `sys.path`, so sibling scripts can `import state` / `import probe`.

Fixed by this skill's own layout (`skill/scripts/` always sits exactly two
directories below the example root) — not a generic walk-up-for-a-marker
search, which would solve a problem this specific, versioned directory layout
doesn't have (and would duplicate `state.py`'s own, differently-scoped
`repo_root()`). Import this before anything else: `import root  # noqa: F401`.
"""

from __future__ import annotations

import sys
from pathlib import Path

STAGING_SMOKE_ROOT = (
    Path(__file__).resolve().parents[2]
)  # scripts -> skill -> example root
if str(STAGING_SMOKE_ROOT) not in sys.path:
    sys.path.insert(0, str(STAGING_SMOKE_ROOT))
