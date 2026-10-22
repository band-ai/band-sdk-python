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
CONTRACT_RUNTIME_ROOT = REPO_ROOT / "src" / "remote_agent"
EXAMPLES_ROOT = REPO_ROOT / "examples"
KIT_DIR = REPO_ROOT / "docker" / "band_python_kit"
BAND_MCP_DIR = REPO_ROOT / "packages" / "band-mcp"
# Every shipped source tree, for the repo-wide AST guards.
SHIPPED_SOURCE_ROOTS = (SRC_ROOT, CONTRACT_RUNTIME_ROOT, BAND_MCP_DIR / "src")
# The scripted provider child the room lifecycle tests run.
ROOM_PEER = REPO_ROOT / "tests" / "adapters" / "roompeer.py"
ENV_TEST_FILE = REPO_ROOT / ".env.test"
BUG_HUNTING_SCRIPTS = (
    REPO_ROOT / ".claude" / "skills" / "bug-hunting-via-example" / "scripts"
)
CI_SCRIPTS = REPO_ROOT / ".github" / "scripts"
CONTRACTS_ROOT = REPO_ROOT / "contracts"
CONTRACT_ENGINE_ROOT = CONTRACTS_ROOT / "contract-lab"
CONTRACT_BAND_ENGINE_ROOT = CONTRACTS_ROOT / "engines" / "band-v1" / "contract-lab"
CONTRACT_SPEC_ROOT = CONTRACTS_ROOT / "spec"
CONTRACT_UNICODE_ROOT = CONTRACTS_ROOT / "header-spikes" / "priv" / "unicode16"
CONTRACT_RUNTIME_TESTS = REPO_ROOT / "tests" / "contracts" / "runtime"
CONTRACT_DEPLOY_ROOT = CONTRACTS_ROOT / "deploy"


def host_absolute_path(*parts: str) -> str:
    """A path that ``band.workspaces.is_host_absolute`` accepts on the running
    OS, rooted at the current drive."""
    return str(Path(Path.cwd().anchor).joinpath(*parts))
