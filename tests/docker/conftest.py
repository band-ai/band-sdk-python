from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.docker.sbx_banner import UPDATE_BANNER
from tests.docker.toolkit.docker_cli import (
    BAND_PYTHON_KIT_DOCKERFILE,
    NAME_PREFIX,
    Container,
    Image,
    OciLayout,
    container_ids,
    remove_containers,
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


@pytest.fixture
def container_name_prefix() -> Iterator[str]:
    """A unique container-name prefix; every container under it is reaped."""
    prefix = f"{NAME_PREFIX}-{uuid.uuid4().hex[:8]}"
    yield prefix
    remove_containers(prefix)
    assert container_ids(prefix) == [], f"containers under {prefix} survived cleanup"


@pytest.fixture
def failing_docker(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make every ``docker`` command exit 1 with this stderr diagnostic.

    Only the external Docker CLI is doubled, by a real failing process; every
    other command still runs for real.
    """
    diagnostic = "docker: daemon rejected the request"
    run = subprocess.run

    def docker_peer(command: list[str], **kwargs: Any) -> Any:
        if command[0] == "docker":
            command = [
                sys.executable,
                "-c",
                f"import sys; sys.stderr.write({diagnostic!r}); sys.exit(1)",
            ]
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", docker_peer)
    return diagnostic


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


@pytest.fixture
def sbx_policy_peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An `sbx` on PATH that answers network-policy commands like v0.47.0.

    Its `policy check --json` reply carries the update banner, decides from
    ``SBX_PEER_ALLOWED``, and exits 1 for a denial; any other command fails.
    Returns the file each invocation's argv is appended to as a JSON line.
    """
    binary = tmp_path / "sbx-peer"
    binary.mkdir()
    calls = tmp_path / "sbx-calls.jsonl"
    peer = binary / "sbx"
    peer.write_text(
        f"#!{sys.executable}\n"
        + r"""import json, os, sys
args = sys.argv[1:]
with open(os.environ['SBX_PEER_CALLS'], 'a') as calls:
    calls.write(json.dumps(args) + '\n')
if args[:3] == ['policy', 'check', 'network'] and args[-1] == '--json':
    allowed = os.environ['SBX_PEER_ALLOWED'] == 'true'
    print(json.dumps({'allowed': allowed}) + '\n' + os.environ['SBX_PEER_BANNER'])
    sys.exit(0 if allowed else 1)
if args[:3] in (['policy', 'allow', 'network'], ['policy', 'rm', 'network']):
    sys.exit(0)
print(f'unsupported sbx command: {args}', file=sys.stderr)
sys.exit(2)
"""
    )
    peer.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SBX_PEER_CALLS", str(calls))
    monkeypatch.setenv("SBX_PEER_BANNER", UPDATE_BANNER)
    return calls
