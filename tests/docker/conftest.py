from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.docker.toolkit.docker_cli import BAND_PYTHON_KIT_DOCKERFILE, Container, Image


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
