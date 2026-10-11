from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from band_rest import AsyncRestClient
from dotenv import load_dotenv

from band.client.rest import aclose_rest_client
from tests.docker.toolkit.docker_cli import (
    BAND_PYTHON_KIT_DOCKERFILE,
    Container,
    Image,
    OciLayout,
)
from tests.docker.toolkit.live_agent import live_containerized_echo_agent

# Re-exported (not just imported) so pytest can find them as fixtures — mirrors
# tests/integration/conftest.py's re-export of tests/conftest_integration.py.
# Deliberately NOT importing orphan_sweep/reap_leaked_agents (autouse in
# tests/e2e/baseline/): those would force every test in this directory
# (including the credential-free conflicting-pin test) to require live
# platform settings just by being collected here.
from tests.e2e.baseline.fixtures.capture import reply_capture  # noqa: F401
from tests.e2e.baseline.fixtures.platform import (  # noqa: F401
    baseline_run_id,
    baseline_ws,
    user_ops,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    user_rest_client,
)
from tests.loaders import load_script_module
from tests.paths import ENV_TEST_FILE


@pytest.fixture(scope="session")
def band_python_kit_image() -> Iterator[Image]:
    """Build the band-python-kit image once per session; reused by every test."""
    with Image.build(
        BAND_PYTHON_KIT_DOCKERFILE, tag_prefix="band-python-kit-test"
    ) as image:
        yield image


@pytest.fixture
def band_python_kit_container(band_python_kit_image: Image) -> Iterator[Container]:
    """A running container from the shared image, torn down after each test."""
    with Container.run(
        band_python_kit_image, name_prefix="band-python-kit-test"
    ) as container:
        yield container


@pytest.fixture
async def live_containerized_agent(
    band_python_kit_image: Image,
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
) -> AsyncIterator[tuple[ProvisionedAgent, str]]:
    """A real agent's echo process, running in a real container, plus the
    room it's in — yields ``(agent, room_id)`` for a test to send a turn to."""
    async with live_containerized_echo_agent(
        band_python_kit_image,
        resource_manager,
        baseline_settings,
        label="live-container",
    ) as result:
        yield result


@pytest.fixture(scope="session")
def band_python_kit_layout() -> Iterator[OciLayout]:
    with OciLayout.build() as layout:
        yield layout


@pytest.fixture(scope="session")
def kit_tck(tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("kit-tck") / (
        "kit-tck.exe" if sys.platform == "win32" else "kit-tck"
    )
    installer = load_script_module("scripts/install-kit-tck.py", "kit_tck_installer")
    installer.install(destination)
    return destination


@pytest.fixture(scope="session")
def baseline_settings() -> BaselineSettings:
    """Live Docker checks always use the repository credential source."""
    load_dotenv(ENV_TEST_FILE, override=True)
    return BaselineSettings()


@pytest.fixture(scope="session")
async def baseline_user_client(
    baseline_settings: BaselineSettings,
) -> AsyncIterator[AsyncRestClient]:
    """Check human access before creating resources that it must also reap."""
    client = user_rest_client(baseline_settings)
    try:
        await client.human_api_agents.list_my_agents(request_options={"max_retries": 0})
        yield client
    finally:
        await aclose_rest_client(client)


@pytest.fixture
async def resource_manager(
    baseline_settings: BaselineSettings,
    baseline_user_client: AsyncRestClient,
    baseline_run_id: str,  # noqa: F811 -- pytest injection of re-exported fixture
) -> AsyncIterator[ResourceManager]:
    """Docker acceptance requires confirmed cleanup of every owned resource."""
    if not baseline_settings.run.autoclean:
        raise ValueError("Docker acceptance requires BAND_E2E_AUTOCLEAN=true")
    resources = ResourceManager(
        user_client=baseline_user_client,
        settings=baseline_settings,
        run_id=baseline_run_id,
    )
    try:
        yield resources
    finally:
        await resources.reap_all(strict=True)
