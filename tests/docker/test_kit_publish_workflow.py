"""Structural contracts for the reusable kit publishing workflow."""

from __future__ import annotations

from typing import Any

import yaml

from tests.paths import REPO_ROOT

DOCKERHUB_SECRETS = {"DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"}
CALLER_WORKFLOWS = (
    "release.yml",
    "kit-image-rebuild.yml",
    "kit-publish-manual.yml",
)


def load_workflow(name: str) -> dict[str, Any]:
    return yaml.load(
        (REPO_ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_reusable_workflow_declares_dockerhub_secrets() -> None:
    workflow = load_workflow("kit-publish.yml")
    declared = workflow["on"]["workflow_call"]["secrets"]

    assert set(declared) == DOCKERHUB_SECRETS
    assert all(secret["required"] == "true" for secret in declared.values())


def test_every_caller_forwards_only_the_dockerhub_secrets() -> None:
    for name in CALLER_WORKFLOWS:
        workflow = load_workflow(name)
        call = next(
            job
            for job in workflow["jobs"].values()
            if job.get("uses") == "./.github/workflows/kit-publish.yml"
        )

        assert set(call["secrets"]) == DOCKERHUB_SECRETS
        for secret in DOCKERHUB_SECRETS:
            assert call["secrets"][secret] == f"${{{{ secrets.{secret} }}}}"


def test_stamped_specs_are_verified_against_the_versioned_image_refs() -> None:
    workflow = load_workflow("kit-publish.yml")
    stamp = next(
        step
        for step in workflow["jobs"]["kit"]["steps"]
        if step.get("name") == "Stamp distribution specs (GHCR + Hub image digests)"
    )

    assert 'grep -qF "${IMAGE_NAME}:${{ inputs.version }}@${digest}"' in stamp["run"]
    assert (
        'grep -qF "${HUB_IMAGE_NAME}:${{ inputs.version }}@${digest}"' in stamp["run"]
    )
