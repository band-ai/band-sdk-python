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

import logging
import shlex
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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
    vscode_window,
)
from tests.e2e.vscode.rooms import SurfaceRoom
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
    "surface_room",
    "user_ops",
    "vscode_settings",
    "vscode_workspace",
]


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
        port=vscode_settings.band_mcp_port,
    )
    await server.start()
    yield server
    await server.stop()


@pytest.fixture(scope="session")
def vscode_workspace(
    vscode_settings: VSCodeChatSettings, band_mcp: BandMCPServer
) -> Path:
    """The (persistent by default) workspace VS Code opens.

    A stable path + stable band-mcp port keep ``.vscode/mcp.json`` identical
    across runs, so VS Code's remembered MCP-server trust holds and reruns
    prompt for nothing. Cell marker files use per-run tokens, so leftovers
    from earlier runs never collide.
    """
    workspace = Path(vscode_settings.vscode_chat_workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    scaffold_workspace(workspace, band_mcp.sse_url)
    return workspace


@pytest.fixture(scope="session")
async def driver(
    vscode_settings: VSCodeChatSettings, vscode_workspace: Path
) -> AsyncGenerator[CodeChatDriver, None]:
    """One ready-to-drive VS Code window for the session (see ``vscode_window``)."""
    try:
        async with vscode_window(
            shlex.split(vscode_settings.code_command), vscode_workspace
        ) as chat_driver:
            yield chat_driver
    except PreflightError as error:
        pytest.fail(str(error))


@pytest.fixture
def surface_room(
    resource_manager: ResourceManager,
    user_ops,
    reply_capture,
    driver: CodeChatDriver,
    copilot_identity: ProvisionedAgent,
    vscode_settings: VSCodeChatSettings,
):
    """Factory: open a provisioned room with the Copilot surface bound.

    ``async with surface_room("recall") as room:`` yields a ``SurfaceRoom``
    (see ``rooms.py``) — the cells' one intent object; all turn plumbing
    (mentions, prompt relay, reply wait) lives behind it.
    """

    @asynccontextmanager
    async def open_room(
        label: str, *, participants: tuple[str, ...] = ()
    ) -> AsyncGenerator[SurfaceRoom, None]:
        room_id = await resource_manager.provision_room(
            title=f"e2e-vscode-{label}",
            participants=[copilot_identity.id, *participants],
        )
        async with reply_capture(room_id) as capture:
            yield SurfaceRoom(
                room_id=room_id,
                capture=capture,
                driver=driver,
                identity=copilot_identity,
                user_ops=user_ops,
                resources=resource_manager,
                turn_timeout=vscode_settings.vscode_chat_timeout,
            )

    return open_room


def pytest_configure(config: pytest.Config) -> None:
    """Register the suite scorecard plugin when emission is enabled."""
    settings = VSCodeChatSettings()
    if not settings.vscode_chat_scorecard_json:
        return
    plugin = VSCodeScorecard(
        settings.vscode_chat_scorecard_json, Path(__file__).parent.resolve()
    )
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
    base = VSCodeChatSettings().vscode_chat_timeout
    for item in items:
        if not Path(item.path).is_relative_to(suite_dir):
            continue
        # Prepended (append=False) so this beats pytest-asyncio's bare
        # auto-mode marker — get_closest_marker takes the first hit, and the
        # bare marker's default function loop would strand the session-scoped
        # WS/REST fixtures on another loop (subscribe timeouts, closed-loop
        # teardown errors).
        item.add_marker(session_marker, append=False)
        timeout = effective_timeout(item, base)
        if timeout is not None:
            item.add_marker(pytest.mark.timeout(timeout), append=False)
