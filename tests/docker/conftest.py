from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.docker.toolkit.docker_cli import BAND_PYTHON_KIT_DOCKERFILE, Container, Image

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
