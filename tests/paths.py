"""Repository filesystem anchors for the test suite.

``REPO_ROOT`` is defined once, here — not re-derived per file with
``Path(__file__).parents[N]`` arithmetic, whose ``N`` silently goes stale when
a file moves or the tree is reorganized. Import these anchors for anything
addressed from the repository root; a path that is genuinely package-relative
(a fixture file sitting next to its test) should stay relative to its own
``__file__`` instead.

Deliberately a plain module, not conftest fixtures: most consumers are
import-time constants (drift-test roots, ``load_dotenv`` calls), which
pytest's ``pytestconfig.rootpath`` cannot serve, and importing from a
conftest is unsupported.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SRC_ROOT = REPO_ROOT / "src" / "band"
EXAMPLES_ROOT = REPO_ROOT / "examples"
KIT_DIR = REPO_ROOT / "docker" / "band_python_kit"
ENV_TEST_FILE = REPO_ROOT / ".env.test"
