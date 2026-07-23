"""Pytest wiring for the Copilot-in-VS-Code suite.

Mirrors the baseline conftest's registration style: the shared platform/capture
fixtures are plain imports re-exported via ``__all__`` (``pytest_plugins`` is
not allowed in a non-root conftest), and the hooks here add only what this
suite needs — the gate, the session loop + per-turn timeout markers, and the
suite-local scorecard plugin.

The suite drives a real signed-in VS Code window, so beyond the env gate it has
human prerequisites (see README.md): VS Code installed with the Copilot Chat
extension signed in, and the window the ``driver`` fixture opens left alone.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from band_rest import AsyncRestClient

from tests.e2e.baseline.fixtures.capture import judge, reply_capture
from tests.e2e.baseline.fixtures.platform import (
    baseline_run_id,
    baseline_settings,
    baseline_user_client,
    baseline_ws,
    orphan_sweep,
    reap_leaked_agents,
    resource_manager,
    user_ops,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.vscode.driver import (
    CodeChatDriver,
    PreflightError,
    capture_versions,
    preflight,
)
from tests.e2e.vscode.scorecard import VSCodeScorecard
from tests.e2e.vscode.server import BandMCPServer
from tests.e2e.vscode.settings import VSCodeChatSettings
from tests.e2e.vscode.workspace import scaffold_workspace
from tests.toolkit.timeouts import effective_timeout

logger = logging.getLogger(__name__)

# Re-exported fixtures (defined in the baseline fixtures package).
__all__ = [
    "band_mcp",
    "baseline_run_id",
    "baseline_settings",
    "baseline_user_client",
    "baseline_ws",
    "copilot_identity",
    "driver",
    "judge",
    "orphan_sweep",
    "reap_leaked_agents",
    "reply_capture",
    "resource_manager",
    "user_ops",
    "vscode_settings",
    "vscode_workspace",
]

# Seconds after `code <workspace>` for the window, extension host, and MCP
# client to come up before the first prompt is submitted.
WINDOW_OPEN_GRACE_S = 20


@pytest.fixture(scope="session")
def vscode_settings() -> VSCodeChatSettings:
    return VSCodeChatSettings()


@pytest.fixture(scope="session")
async def copilot_identity(
    baseline_settings: BaselineSettings,
    baseline_user_client: AsyncRestClient,
    baseline_run_id: str,
) -> AsyncGenerator[ProvisionedAgent, None]:
    """The one Band agent identity Copilot acts as, held for the whole session.

    Session-scoped (its own manager, not the per-test ``resource_manager``)
    because the identity's key is baked into the band-mcp subprocess env and
    the workspace's MCP config at session start.
    """
    resources = ResourceManager(
        user_client=baseline_user_client,
        settings=baseline_settings,
        run_id=baseline_run_id,
    )
    identity = await resources.provision_agent("copilot")
    yield identity
    if baseline_settings.run.autoclean:
        await resources.reap_all()


@pytest.fixture(scope="session")
async def band_mcp(
    vscode_settings: VSCodeChatSettings,
    baseline_settings: BaselineSettings,
    copilot_identity: ProvisionedAgent,
) -> AsyncGenerator[BandMCPServer, None]:
    server = BandMCPServer(
        shlex.split(vscode_settings.band_mcp_command),
        agent_key=copilot_identity.api_key,
        base_url=baseline_settings.endpoints.rest_url,
    )
    await server.start()
    yield server
    await server.stop()


@pytest.fixture(scope="session")
def vscode_workspace(
    tmp_path_factory: pytest.TempPathFactory, band_mcp: BandMCPServer
) -> Path:
    workspace = tmp_path_factory.mktemp("vscode-chat-ws")
    scaffold_workspace(workspace, band_mcp.sse_url)
    return workspace


@pytest.fixture(scope="session")
async def driver(
    vscode_settings: VSCodeChatSettings, vscode_workspace: Path
) -> CodeChatDriver:
    """Preflight the VS Code CLI, open the workspace window once, yield the driver."""
    code_command = shlex.split(vscode_settings.code_command)
    try:
        preflight(code_command)
    except PreflightError as error:
        pytest.fail(str(error))
    chat_driver = CodeChatDriver(code_command, vscode_workspace)
    await chat_driver.open_window()
    await asyncio.sleep(WINDOW_OPEN_GRACE_S)
    return chat_driver


def pytest_configure(config: pytest.Config) -> None:
    """Register the suite scorecard plugin when emission is enabled."""
    settings = VSCodeChatSettings()
    if not settings.scorecard_json:
        return
    plugin = VSCodeScorecard(settings.scorecard_json, Path(__file__).parent.resolve())
    plugin.metadata = capture_versions(
        shlex.split(settings.code_command), shlex.split(settings.band_mcp_command)
    )
    config.pluginmanager.register(plugin)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Self-gate (besides the root markers) and fail loud on missing credentials."""
    if not Path(item.path).is_relative_to(Path(__file__).parent):
        return
    if not VSCodeChatSettings().vscode_chat_tests_enabled:
        pytest.skip("VSCODE_CHAT_TESTS_ENABLED is not true")
    if not BaselineSettings().credentials.api_key_user:
        pytest.fail("BAND_API_KEY_USER not set (VS Code chat suite enabled)")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Put suite tests on the session loop and give them the live-turn timeout.

    Same two markers the baseline conftest applies to its own subtree: the
    session-scoped WS/REST fixtures live on the session loop, and live turns
    need far more than the 30s pyproject default.
    """
    suite_dir = Path(__file__).parent
    session_marker = pytest.mark.asyncio(loop_scope="session")
    base = VSCodeChatSettings().turn_timeout
    for item in items:
        if not Path(item.path).is_relative_to(suite_dir):
            continue
        item.add_marker(session_marker)
        timeout = effective_timeout(item, base)
        if timeout is not None:
            item.add_marker(pytest.mark.timeout(timeout), append=False)
