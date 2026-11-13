from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.docker.toolkit.docker_cli import (
    BAND_PYTHON_KIT_DOCKERFILE,
    Container,
    Image,
    OciLayout,
)
from tests.loaders import load_script_module


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


@pytest.fixture
def graph() -> dict[str, dict[str, Any]]:
    validator = load_script_module("scripts/validate-workload.py", "graph_validator")
    descriptor = json.dumps(
        {"schemaVersion": "3", "kind": "workload", "capabilities": []}
    )
    documents: dict[str, dict[str, Any]] = {"root": {"manifests": []}}
    for architecture in ("amd64", "arm64"):
        digest = f"sha256:{architecture}"
        documents["root"]["manifests"].append(
            {
                "digest": digest,
                "platform": {"os": "linux", "architecture": architecture},
            }
        )
        documents[digest] = {
            "annotations": {validator.DESCRIPTOR: descriptor},
            "config": {"digest": f"sha256:{architecture}-config"},
        }
        documents[f"sha256:{architecture}-config"] = {
            "config": {
                "User": "agent",
                "Entrypoint": [
                    "/bin/bash",
                    "-c",
                    'exec "$BAND_SDK_PYTHON" -m band.docker.launcher',
                ],
                "Labels": {
                    "org.opencontainers.image.source": "https://github.com/band-ai/band-sdk-python",
                    "org.opencontainers.image.version": "1.5.0",
                    "org.opencontainers.image.revision": "frozen-revision",
                },
            }
        }
        documents["root"]["manifests"].append(
            {
                "digest": f"sha256:{architecture}-attestation",
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": digest,
                },
            }
        )
        documents[f"sha256:{architecture}-attestation"] = {
            "layers": [
                {"annotations": {"in-toto.io/predicate-type": predicate}}
                for predicate in (
                    "https://slsa.dev/provenance/v0.2",
                    "https://spdx.dev/Document",
                )
            ]
        }
    return documents
