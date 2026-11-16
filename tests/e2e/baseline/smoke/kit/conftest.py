"""The band-python-kit image and the live echo agent its smoke runs inside it."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.docker.toolkit.docker_cli import BAND_PYTHON_KIT_DOCKERFILE, Image
from tests.docker.toolkit.live_agent import live_containerized_echo_agent
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.toolkit.tasks import off_loop


@pytest.fixture(scope="session")
async def band_python_kit_image() -> AsyncIterator[Image]:
    """Build the kit image once per session; removed after the last smoke."""
    build = Image.build(BAND_PYTHON_KIT_DOCKERFILE, tag_prefix="band-python-kit-e2e")
    async with off_loop(build) as image:
        yield image


@pytest.fixture
async def live_containerized_agent(
    band_python_kit_image: Image,
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
) -> AsyncIterator[tuple[ProvisionedAgent, str]]:
    """An echo agent running the baked SDK in a real container, plus its room."""
    async with live_containerized_echo_agent(
        band_python_kit_image,
        resource_manager,
        baseline_settings,
        label="live-container",
    ) as result:
        yield result
