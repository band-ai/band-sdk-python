#!/usr/bin/env python3
"""Fail closed unless a registry index contains the complete Band v3 workload."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)
DESCRIPTOR = "vnd.docker.sandbox.kit.descriptor"
PLATFORMS = {("linux", "amd64"), ("linux", "arm64")}


def fetch_json(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "no diagnostic output").strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}") from None
    return json.loads(result.stdout)


def repository_name(reference: str) -> str:
    """Remove a tag or digest while retaining registry ports."""
    repository = reference.partition("@")[0]
    if ":" in repository.rsplit("/", 1)[-1]:
        return repository.rsplit(":", 1)[0]
    return repository


def is_v3_workload(reference: str, *, release_version: str | None = None) -> bool:
    """Classify legacy metadata without hiding registry access failures."""
    repository = repository_name(reference)
    root = fetch_json(["oras", "manifest", "fetch", reference])
    if root.get("artifactType"):
        return False
    manifest = root
    if root.get("manifests"):
        child = next(
            (
                item
                for item in root["manifests"]
                if item.get("platform", {}).get("os") == "linux"
            ),
            None,
        )
        if child is None:
            return False
        manifest = fetch_json(
            ["oras", "manifest", "fetch", f"{repository}@{child['digest']}"]
        )
    descriptor = json.loads(manifest.get("annotations", {}).get(DESCRIPTOR, "{}"))
    if descriptor.get("schemaVersion") != "3" or descriptor.get("kind") != "workload":
        return False
    if release_version is not None:
        config = fetch_json(
            [
                "oras",
                "blob",
                "fetch",
                "--output",
                "-",
                f"{repository}@{manifest['config']['digest']}",
            ]
        )
        version = (
            config.get("config", {})
            .get("Labels", {})
            .get("org.opencontainers.image.version", "")
        )
        return version.split("-r", 1)[0] == release_version
    return True


def validate_workload(
    reference: str,
    *,
    fetch: Callable[[list[str]], dict[str, Any]] = fetch_json,
    version: str | None = None,
    revision: str | None = None,
) -> None:
    """Inspect platform descriptors, runtime configs and supply-chain children."""
    root = fetch(["oras", "manifest", "fetch", reference])
    if root.get("artifactType") or not root.get("manifests"):
        raise ValueError("expected a multi-platform workload index")
    repository = repository_name(reference)
    platforms: set[tuple[str, str]] = set()
    descriptors: list[dict[str, Any]] = []
    attestations: dict[str, set[str]] = {}
    platform_digests: set[str] = set()
    for child in root["manifests"]:
        manifest = fetch(
            ["oras", "manifest", "fetch", f"{repository}@{child['digest']}"]
        )
        platform = child.get("platform", {})
        pair = (platform.get("os", ""), platform.get("architecture", ""))
        if pair not in PLATFORMS:
            if (
                child.get("annotations", {}).get("vnd.docker.reference.type")
                != "attestation-manifest"
            ):
                raise ValueError("unexpected non-platform manifest")
            subject = child.get("annotations", {}).get(
                "vnd.docker.reference.digest", ""
            )
            predicates = attestations.setdefault(subject, set())
            for layer in manifest.get("layers", []):
                predicate = layer.get("annotations", {}).get(
                    "in-toto.io/predicate-type", ""
                )
                if "slsa.dev/provenance" in predicate:
                    predicates.add("provenance")
                if "spdx.dev/Document" in predicate:
                    predicates.add("sbom")
            continue
        if pair in platforms:
            raise ValueError("duplicate workload platform")
        platforms.add(pair)
        platform_digests.add(child["digest"])
        descriptor = json.loads(manifest.get("annotations", {}).get(DESCRIPTOR, "{}"))
        if (
            descriptor.get("schemaVersion") != "3"
            or descriptor.get("kind") != "workload"
        ):
            raise ValueError("immutable target is not a v3 workload")
        descriptors.append(descriptor)
        config = fetch(
            [
                "oras",
                "blob",
                "fetch",
                "--output",
                "-",
                f"{repository}@{manifest['config']['digest']}",
            ]
        )
        runtime = config.get("config", {})
        if runtime.get("User") != "agent":
            raise ValueError("workload must run as agent")
        labels = runtime.get("Labels", {})
        if (
            labels.get("org.opencontainers.image.source")
            != "https://github.com/band-ai/band-sdk-python"
        ):
            raise ValueError("workload has incompatible source metadata")
        if (
            version is not None
            and labels.get("org.opencontainers.image.version") != version
        ):
            raise ValueError("immutable target has incompatible version metadata")
        if (
            revision is not None
            and labels.get("org.opencontainers.image.revision") != revision
        ):
            raise ValueError("immutable target has incompatible source revision")
        command = runtime.get("Entrypoint", []) + runtime.get("Cmd", [])
        direct = ["/opt/band/venv/bin/python", "-m", "band.docker.launcher"]
        configured = [
            "/bin/bash",
            "-c",
            'exec "$BAND_SDK_PYTHON" -m band.docker.launcher',
        ]
        if command not in (direct, configured):
            raise ValueError("workload must use the Band launcher")
    if platforms != PLATFORMS:
        raise ValueError("both linux/amd64 and linux/arm64 are required")
    if descriptors[0] != descriptors[1]:
        raise ValueError("platform descriptors disagree")
    index_descriptor = root.get("annotations", {}).get(DESCRIPTOR)
    if index_descriptor and json.loads(index_descriptor) != descriptors[0]:
        raise ValueError("index descriptor disagrees with platform manifests")
    if any(
        attestations.get(digest) != {"provenance", "sbom"}
        for digest in platform_digests
    ):
        raise ValueError("provenance and SBOM children are required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference")
    parser.add_argument("--version")
    parser.add_argument("--revision")
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--release-version")
    args = parser.parse_args()
    if args.schema_only:
        raise SystemExit(
            0
            if is_v3_workload(args.reference, release_version=args.release_version)
            else 3
        )
    validate_workload(args.reference, version=args.version, revision=args.revision)
    logger.info("Validated workload %s", args.reference)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
