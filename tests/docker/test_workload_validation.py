"""Publication refuses incompatible or incomplete immutable OCI graphs."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.loaders import load_script_module

validator = load_script_module("scripts/validate-workload.py", "workload_validation")


@pytest.fixture
def graph() -> dict[str, dict[str, Any]]:
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
