"""Publication refuses incompatible or incomplete immutable OCI graphs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.docker.toolkit import docker_cli
from tests.docker.toolkit.docker_cli import OciLayout
from tests.loaders import load_script_module

validator = load_script_module("scripts/validate-workload.py", "workload_validation")


def validate(graph: dict[str, dict[str, Any]], *, version: str = "1.5.0") -> None:
    def fetch(command: list[str]) -> dict[str, Any]:
        key = command[-1].partition("@")[2] or "root"
        return graph[key]

    validator.validate_workload(
        "registry.example/workload:1.5.0",
        fetch=fetch,
        version=version,
        revision="frozen-revision",
    )


def test_manifest_descriptor_fallback_is_accepted(
    graph: dict[str, dict[str, Any]],
) -> None:
    validate(graph)


@pytest.mark.parametrize(
    "defect",
    [
        "legacy",
        "root",
        "launcher",
        "version",
        "revision",
        "descriptor",
        "attestation",
        "platform",
        "index",
    ],
)
def test_incompatible_immutable_graph_is_rejected(
    graph: dict[str, dict[str, Any]], defect: str
) -> None:
    match defect:
        case "legacy":
            graph["sha256:amd64"]["annotations"][validator.DESCRIPTOR] = (
                '{"schemaVersion":"2"}'
            )
        case "root":
            graph["sha256:amd64-config"]["config"]["User"] = "root"
        case "launcher":
            graph["sha256:amd64-config"]["config"]["Entrypoint"] = ["bash"]
        case "version":
            graph["sha256:amd64-config"]["config"]["Labels"][
                "org.opencontainers.image.version"
            ] = "1.4.0"
        case "revision":
            graph["sha256:amd64-config"]["config"]["Labels"][
                "org.opencontainers.image.revision"
            ] = "different-revision"
        case "descriptor":
            descriptor = json.loads(
                graph["sha256:arm64"]["annotations"][validator.DESCRIPTOR]
            )
            descriptor["displayName"] = "Different"
            graph["sha256:arm64"]["annotations"][validator.DESCRIPTOR] = json.dumps(
                descriptor
            )
        case "attestation":
            graph["sha256:arm64-attestation"]["layers"] = []
        case "platform":
            graph["root"]["manifests"] = graph["root"]["manifests"][:2]
        case "index":
            graph["root"]["annotations"] = {validator.DESCRIPTOR: "{}"}
    with pytest.raises(ValueError):
        validate(graph)


@pytest.mark.parametrize(
    "version,eligible", [("1.5.0", True), ("1.5.0-r2", True), ("1.4.9", False)]
)
def test_weekly_requires_this_release_to_have_completed_promotion(
    graph: dict[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    eligible: bool,
) -> None:
    graph["sha256:amd64-config"]["config"]["Labels"][
        "org.opencontainers.image.version"
    ] = version

    def fetch(command: list[str]) -> dict[str, Any]:
        return graph[command[-1].partition("@")[2] or "root"]

    monkeypatch.setattr(validator, "fetch_json", fetch)
    assert (
        validator.is_v3_workload(
            "registry.example/workload:latest", release_version="1.5.0"
        )
        is eligible
    )


def test_legacy_floating_artifact_is_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validator,
        "fetch_json",
        lambda command: {"artifactType": "application/vnd.docker.sandbox.kit.v2"},
    )
    assert not validator.is_v3_workload("registry.example/workload:latest")


@pytest.mark.parametrize(
    "reference,repository",
    [
        ("registry.example/workload:1.5.0", "registry.example/workload"),
        ("registry.example/workload@sha256:abc", "registry.example/workload"),
        ("localhost:5000/workload:1.5.0", "localhost:5000/workload"),
        ("localhost:5000/workload@sha256:abc", "localhost:5000/workload"),
    ],
)
def test_child_references_preserve_the_registry(
    reference: str, repository: str
) -> None:
    assert validator.repository_name(reference) == repository


def test_registry_failure_preserves_the_cli_diagnostic() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('manifest unknown for candidate'); sys.exit(3)",
    ]
    with pytest.raises(RuntimeError, match="manifest unknown for candidate"):
        validator.fetch_json(command)


def test_layout_failure_exposes_tck_diagnostic(
    tmp_path: Path, graph: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = tmp_path / "blobs" / "sha256"
    blobs.mkdir(parents=True)
    (tmp_path / "index.json").write_text(
        json.dumps({"manifests": [{"digest": "sha256:root"}]})
    )
    for digest, document in graph.items():
        (blobs / digest.removeprefix("sha256:")).write_text(json.dumps(document))
    (tmp_path / "validate").write_text(
        "import sys; sys.stderr.write('TCK: staged descriptor disagrees'); sys.exit(9)"
    )
    monkeypatch.chdir(tmp_path)
    layout = OciLayout(tmp_path, "validation", "1.5.0", "frozen-revision")
    with pytest.raises(RuntimeError, match="TCK: staged descriptor disagrees"):
        layout.validate(tck=Path(sys.executable))


def test_oci_build_failure_exposes_builder_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = docker_cli.subprocess.run

    def builder_peer(command: list[str], **kwargs: Any) -> Any:
        if command[:3] == ["docker", "buildx", "build"]:
            command = [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('Buildx: frontend rejected descriptor'); sys.exit(8)",
            ]
        return run(command, **kwargs)

    monkeypatch.setattr(docker_cli.subprocess, "run", builder_peer)
    with (
        pytest.raises(RuntimeError, match="Buildx: frontend rejected descriptor"),
        OciLayout.build(),
    ):
        pytest.fail("failed build must not yield a layout")
