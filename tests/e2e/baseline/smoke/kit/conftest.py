"""The band-python-kit image and the live agents its smokes run inside it."""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.docker.toolkit.docker_cli import BAND_PYTHON_KIT_DOCKERFILE, Image
from tests.docker.toolkit.live_agent import live_containerized_echo_agent
from tests.docker.toolkit.sandbox_agent import (
    prepare_workspace,
    proxy_managed_sandbox,
    self_registered_sandbox,
)
from tests.docker.toolkit.sbx_cli import Sandbox
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


@pytest.fixture
async def custody_sandbox(
    tmp_path: Path,
    band_python_kit_image: Image,
    baseline_settings: BaselineSettings,
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[Sandbox, str]]:
    """A proxy-managed sandbox, plus the real key the proxy injects for it.

    The fixture stores the injection itself, so the value the absence check
    asserts-absent is exactly the one the proxy would inject. Indirect
    ``False`` leaves the sandbox unattached (its foreground agent not started).
    Yields ``(sandbox, real_key)``.
    """
    real_key = baseline_settings.credentials.api_key_user
    workspace = prepare_workspace(tmp_path, endpoints=baseline_settings.endpoints)
    sandbox_cm = proxy_managed_sandbox(
        workspace=workspace,
        band_key=real_key,
        endpoints=baseline_settings.endpoints,
        image=band_python_kit_image,
        attached=getattr(request, "param", True),
    )
    async with off_loop(sandbox_cm) as sandbox:
        yield sandbox, real_key


@pytest.fixture
async def round_trip_sandbox(
    tmp_path: Path,
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
    band_python_kit_image: Image,
) -> AsyncIterator[tuple[ProvisionedAgent, str, Sandbox]]:
    """A live echo agent inside an unattached proxy-managed sandbox, plus its room.

    The agent's *key* is injected host-side, so the VM holds only the sentinel;
    the test owns each foreground session. Yields ``(agent, room_id, sandbox)``.
    """
    agent = await resource_manager.provision_agent("nevervm-roundtrip")
    room_id = await resource_manager.provision_room(participants=[agent.id])
    workspace = prepare_workspace(
        tmp_path, agent_id=agent.id, endpoints=baseline_settings.endpoints
    )
    sandbox_cm = proxy_managed_sandbox(
        workspace=workspace,
        band_key=agent.api_key,
        endpoints=baseline_settings.endpoints,
        image=band_python_kit_image,
        attached=False,
    )
    async with off_loop(sandbox_cm) as sandbox:
        yield agent, room_id, sandbox


@pytest.fixture
async def self_registered_agent(
    tmp_path: Path,
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
    band_python_kit_image: Image,
) -> AsyncIterator[tuple[str, str, argparse.Namespace]]:
    """An agent ``band-kit provision`` registered, running in its sandbox."""
    async with self_registered_sandbox(
        tmp_path, resource_manager, baseline_settings, band_python_kit_image
    ) as result:
        yield result
