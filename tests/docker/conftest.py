from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator, Iterator
from types import ModuleType

import pytest

from tests.docker.toolkit.docker_cli import BAND_PYTHON_KIT_DOCKERFILE, Container, Image
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
    baseline_settings,
    baseline_user_client,
    baseline_ws,
    resource_manager,
    user_ops,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.paths import REPO_ROOT


def load_script_module(relative_path: str, module_name: str) -> ModuleType:
    """Import a ``scripts/`` file whose hyphenated name isn't a valid module.

    Release helpers live in ``scripts/`` with hyphenated filenames (the repo's
    naming rule), so they can't be reached with a plain ``import``. Load them by
    path for off-runner unit testing of their logic.
    """
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def stamp_kit_spec() -> ModuleType:
    """The ``scripts/stamp-kit-spec.py`` release helper, loaded by path."""
    return load_script_module("scripts/stamp-kit-spec.py", "stamp_kit_spec")


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
    resource_manager: ResourceManager,  # noqa: F811 -- pytest fixture injection, not the import above
    baseline_settings: BaselineSettings,  # noqa: F811 -- same
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
