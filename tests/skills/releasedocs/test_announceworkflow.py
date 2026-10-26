"""release-announce.yml runs real scripts and reads only the outputs they write.

A drifted output name makes a gate like ``proceed == 'true'`` silently false, so
every release would skip announcing with a green run and no alert.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from tests.paths import RELEASE_DOCS_SCRIPTS, RELEASE_DOCS_SKILL, REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-announce.yml"
SKILL_CHECKOUT = "/${{ env.SKILL }}/"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow() -> dict[str, Any]:
    # BaseLoader keeps `on` a string key instead of YAML 1.1's boolean True.
    return yaml.load(workflow_text(), Loader=yaml.BaseLoader)


def all_steps() -> list[dict[str, Any]]:
    return [step for job in workflow()["jobs"].values() for step in job["steps"]]


def step_run(step: str) -> str:
    """The ``run`` script of the step whose ``id`` is ``step``."""
    steps = all_steps()
    (run,) = [s["run"] for s in steps if s.get("id") == step]
    return run


def outputs_read(step: str) -> set[str]:
    return set(re.findall(rf"steps\.{step}\.outputs\.(\w+)", workflow_text()))


def outputs_written(script: str) -> set[str]:
    """The keys ``script`` passes to ``append_outputs``."""
    tree = ast.parse((RELEASE_DOCS_SCRIPTS / f"{script}.py").read_text("utf-8"))
    return {
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "append_outputs"
        for keyword in node.keywords
        if keyword.arg is not None
    }


class TestAnnounceWorkflow:
    def test_every_script_it_runs_exists(self) -> None:
        scripts = set(re.findall(r"\$SKILL/scripts/(\w+\.py)", workflow_text()))
        assert scripts
        assert {
            name for name in scripts if not (RELEASE_DOCS_SCRIPTS / name).is_file()
        } == set()

    @pytest.mark.parametrize(
        ("step", "script"), [("collect", "collectprs"), ("build", "builditems")]
    )
    def test_gates_read_only_outputs_the_script_writes(
        self, step: str, script: str
    ) -> None:
        assert f"$SKILL/scripts/{script}.py" in step_run(step)
        read = outputs_read(step)
        assert read
        assert read - outputs_written(script) == set()

    def test_every_checkout_of_the_skill_fetches_it(self) -> None:
        assert REPO_ROOT / workflow()["env"]["SKILL"] == RELEASE_DOCS_SKILL
        sparse = [
            step["with"]["sparse-checkout"]
            for step in all_steps()
            if "sparse-checkout" in step.get("with", {})
        ]
        assert sparse
        assert set(sparse) == {SKILL_CHECKOUT}

    def test_claude_gets_only_the_read_tools(self) -> None:
        (claude,) = [s for s in all_steps() if s.get("id") == "claude"]
        flags = claude["with"]["claude_args"].split()
        assert {"--restricted", "--strict-mcp-config"} <= set(flags)
        assert flags[flags.index("--tools") + 1] == "Read,Glob,Grep"

    def test_announcement_targets_exist(self, announcement: ModuleType) -> None:
        targets = set(re.findall(r"--target ([\w-]+)", workflow_text()))
        assert targets == {target.value for target in announcement.Target}

    def test_bundle_carries_the_asset_the_live_guard_looks_for(
        self, collectprs: ModuleType
    ) -> None:
        notes = re.search(r'builditems\.py".*?--out (\S+)', workflow_text(), re.DOTALL)
        assert notes is not None
        assert Path(notes[1]).name == collectprs.ANNOUNCED_ASSET
