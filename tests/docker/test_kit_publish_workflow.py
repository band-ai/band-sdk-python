"""Structural contracts for the reusable kit publishing workflow."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
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


def publishing_steps() -> list[dict[str, Any]]:
    return load_workflow("kit-publish.yml")["jobs"]["image"]["steps"]


def test_legacy_source_guard_precedes_registry_mutation() -> None:
    steps = publishing_steps()
    guard = next(
        i
        for i, step in enumerate(steps)
        if step.get("name", "").startswith("Reject legacy")
    )
    build = next(
        i
        for i, step in enumerate(steps)
        if step.get("name", "").startswith("Build and push")
    )
    assert guard < build
    assert 'spec.get("schemaVersion")' in steps[guard]["run"]
    assert 'spec.get("kind") != "workload"' in steps[guard]["run"]


def test_publish_uses_one_descriptor_and_no_legacy_image_aliases() -> None:
    workflow = load_workflow("kit-publish.yml")
    assert set(workflow["env"]) == {"KIT_NAME", "HUB_KIT_NAME"}
    build = next(
        step
        for step in publishing_steps()
        if step.get("name", "").startswith("Build and push")
    )
    assert build["with"]["file"] == "docker/band_python_kit/spec.yaml"
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert build["with"]["provenance"] == "mode=max"
    assert build["with"]["sbom"] == "true"


def test_partial_mirror_copies_the_complete_digest_graph_after_validation() -> None:
    repair = next(
        step
        for step in publishing_steps()
        if step.get("name") == "Complete a partial image publish"
    )
    script = repair["run"]
    assert script.index("validate-workload.py") < script.index("oras cp --recursive")
    assert '"${source_repo}@${digest}" "$target_ref"' in script
    assert 'oras resolve "$target_ref"' in script
    assert "imagetools create" not in script
    oras = next(
        step for step in publishing_steps() if step.get("name") == "Install ORAS"
    )
    assert oras["with"]["version"] == "1.3.0"


def test_initial_rollout_holds_release_floats_and_weekly_requires_promotion() -> None:
    release = load_workflow("release.yml")
    publish = next(
        job
        for job in release["jobs"].values()
        if job.get("uses") == "./.github/workflows/kit-publish.yml"
    )
    assert publish["with"]["move-floating"] == "false"
    rebuild = load_workflow("kit-image-rebuild.yml")
    steps = rebuild["jobs"]["decide"]["steps"]
    eligibility = next(step for step in steps if step.get("id") == "eligibility")
    assert "released-spec.yaml" in eligibility["run"]
    assert '"$IMAGE_NAME:latest"' in eligibility["run"]
    assert '"$HUB_IMAGE_NAME:latest"' in eligibility["run"]
    gate = next(step for step in steps if step.get("id") == "gate")
    assert "steps.eligibility.outputs.eligible" in gate["run"]


@pytest.mark.parametrize(
    "descriptor,accepted",
    [
        ({"schemaVersion": "2", "kind": "workload"}, False),
        ({"schemaVersion": "3", "kind": "mixin"}, False),
        ({"schemaVersion": "3", "kind": "workload"}, True),
    ],
)
def test_source_guard_executes_before_publication(
    tmp_path: Path, descriptor: dict[str, str], accepted: bool
) -> None:
    guard = next(
        step
        for step in publishing_steps()
        if step.get("name", "").startswith("Reject legacy")
    )
    source = guard["run"].partition("\n")[2].removesuffix("PYTHON\n")
    directory = tmp_path / "docker" / "band_python_kit"
    directory.mkdir(parents=True)
    (directory / "spec.yaml").write_text(yaml.safe_dump(descriptor), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted
    if not accepted:
        assert "ineligible: source must contain a v3 workload" in result.stderr


@pytest.mark.skipif(
    os.name != "posix", reason="Publisher uses Bash and executable POSIX CLI peers"
)
@pytest.mark.parametrize("tck_accepts", [False, True])
def test_mirror_recovery_rejects_tck_invalid_source_before_writes(
    tmp_path: Path,
    graph: dict[str, dict[str, Any]],
    tck_accepts: bool,
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    (tmp_path / "graph.json").write_text(json.dumps(graph))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(
        REPO_ROOT / "scripts" / "validate-workload.py", scripts / "validate-workload.py"
    )
    peers = {
        "python3": f"import os,sys; os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])",
        "kit-tck": f"import sys; sys.stderr.write('TCK rejects staged descriptor'); sys.exit({0 if tck_accepts else 9})",
        "oras": r"""import json,sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] in (['manifest','fetch'], ['blob','fetch']):
    graph = json.loads(Path('graph.json').read_text())
    print(json.dumps(graph[args[-1].partition('@')[2] or 'root']))
elif args[0] == 'resolve':
    print('sha256:root')
elif args[:2] == ['cp','--recursive']:
    Path('target-published').write_text(args[-1])
else:
    sys.exit(5)
""",
    }
    for name, source in peers.items():
        executable = binary / name
        executable.write_text(f"#!{sys.executable}\n{source}\n")
        executable.chmod(0o755)
    repair = next(
        step
        for step in publishing_steps()
        if step.get("name") == "Complete a partial image publish"
    )
    values = {
        "steps.image_state.outputs.image_exists": "true",
        "inputs.version": "1.5.0",
        "steps.source.outputs.revision": "frozen-revision",
    }
    script = re.sub(
        r"\$\{\{\s*(.*?)\s*\}\}", lambda match: values[match.group(1)], repair["run"]
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=os.environ
        | {
            "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
            "IMAGE_REF": "ghcr.test/workload:1.5.0",
            "HUB_IMAGE_REF": "hub.test/workload:1.5.0",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert (result.returncode == 0) is tck_accepts
    assert (tmp_path / "target-published").exists() is tck_accepts
    if not tck_accepts:
        assert "TCK rejects staged descriptor" in result.stderr
