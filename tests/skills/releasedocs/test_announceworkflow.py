"""release-announce.yml runs real scripts and reads only the outputs they write.

A drifted output name makes a gate like ``proceed == 'true'`` silently false, so
every release would skip announcing with a green run and no alert.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from tests.paths import RELEASE_DOCS_SCRIPTS, RELEASE_DOCS_SKILL, REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-announce.yml"
SKILL_CHECKOUT = "/${{ env.SKILL }}/"
CLAUDE_ACTION = "anthropics/claude-code-action"
TOKEN_ACTION = "actions/create-github-app-token"
UPLOAD_ACTION = "actions/upload-artifact"
# The jobs that read private Fern content.
FERN_JOBS = ("holdbacks", "fern")
REVOKE = "gh api --method DELETE /installation/token"
# The fern job runs the scripts from a checkout moved out of the workspace.
SCRIPT_RUN = re.compile(r"(?:\$SKILL/scripts|\$SCRIPTS)/(\w+\.py)")


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


def job(name: str) -> dict[str, Any]:
    return workflow()["jobs"][name]


def needs(name: str) -> set[str]:
    value = job(name)["needs"]
    return {value} if isinstance(value, str) else set(value)


def step_position(name: str, matches: Callable[[dict[str, Any]], bool]) -> int:
    """The index of the only step in job ``name`` that ``matches``."""
    (index,) = [i for i, step in enumerate(job(name)["steps"]) if matches(step)]
    return index


def uses(action: str) -> Callable[[dict[str, Any]], bool]:
    return lambda step: step.get("uses", "").startswith(f"{action}@")


def has_id(step_id: str) -> Callable[[dict[str, Any]], bool]:
    return lambda step: step.get("id") == step_id


def claude_steps() -> dict[str, dict[str, Any]]:
    """The Claude steps that read Fern content, by job."""
    return {
        name: job(name)["steps"][step_position(name, uses(CLAUDE_ACTION))]
        for name in FERN_JOBS
    }


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
        scripts = set(SCRIPT_RUN.findall(workflow_text()))
        assert scripts
        assert {
            name for name in scripts if not (RELEASE_DOCS_SCRIPTS / name).is_file()
        } == set()

    @pytest.mark.parametrize(
        ("step", "script"),
        [
            ("collect", "collectprs"),
            ("build", "builditems"),
            ("holdbacks", "holdbacks"),
            ("state", "fernstate"),
            ("patch", "checkpatch"),
        ],
    )
    def test_gates_read_only_outputs_the_script_writes(
        self, step: str, script: str
    ) -> None:
        assert SCRIPT_RUN.findall(step_run(step)) == [f"{script}.py"]
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

    @pytest.mark.parametrize("name", ["bundle", "slack", "discord"])
    def test_nothing_is_attached_or_posted_before_holdbacks(self, name: str) -> None:
        assert {"changelog", "holdbacks"} <= needs(name)
        assert job(name)["if"] == "needs.holdbacks.outputs.has_entry == 'true'"

    def test_holdbacks_passes_on_only_public_safe_outputs(self) -> None:
        assert set(job("holdbacks")["outputs"]) == {"has_entry", "held"}

    def test_changelog_shares_the_full_notes_and_prints_no_entry(self) -> None:
        (upload,) = [s for s in job("changelog")["steps"] if uses(UPLOAD_ACTION)(s)]
        assert upload["with"]["name"] == "release-notes-full"
        summary = job("changelog")["steps"][-1]["run"]
        assert ("cat " in summary, "/band-release-docs" in summary) == (False, False)

    @pytest.mark.parametrize(
        ("name", "uploads"), [("holdbacks", ["bundle"]), ("fern", [])]
    )
    def test_fern_jobs_upload_only_the_public_bundle(
        self, name: str, uploads: list[str]
    ) -> None:
        steps = [s for s in job(name)["steps"] if uses(UPLOAD_ACTION)(s)]
        assert [step["with"]["name"] for step in steps] == uploads

    @pytest.mark.parametrize("name", FERN_JOBS)
    def test_fern_read_token_is_revoked_before_claude_runs(self, name: str) -> None:
        claude = step_position(name, uses(CLAUDE_ACTION))
        assert step_position(name, has_id("fern-read")) < claude
        assert step_position(name, lambda s: s.get("run") == REVOKE) < claude
        step = job(name)["steps"][claude]
        assert "fern-" not in json.dumps(step)

    def test_fern_git_directory_is_gone_before_the_docs_review(self) -> None:
        drop = step_position("fern", lambda s: s.get("run") == "rm -rf fern/.git")
        assert drop < step_position("fern", uses(CLAUDE_ACTION))

    def test_docs_review_runs_when_an_open_pr_is_refiled(self) -> None:
        review = job("fern")["steps"][step_position("fern", uses(CLAUDE_ACTION))]
        assert review["if"] == "steps.state.outputs.docs != 'skip'"

    def test_held_prs_are_withheld_before_the_docs_review(self) -> None:
        withhold = step_position("fern", lambda s: "withhold.py" in s.get("run", ""))
        assert "> review-prs.json" in job("fern")["steps"][withhold]["run"]
        assert withhold < step_position("fern", uses(CLAUDE_ACTION))

    def test_workspace_is_cleared_between_the_review_and_filing(self) -> None:
        clear = step_position(
            "fern", lambda s: "find . -mindepth 1" in s.get("run", "")
        )
        checkouts = [
            i
            for i, step in enumerate(job("fern")["steps"])
            if uses("actions/checkout")(step)
        ]
        assert step_position("fern", uses(CLAUDE_ACTION)) < clear < checkouts[-1]

    def test_cancelled_runs_file_nothing_and_a_displaced_filing_alerts(self) -> None:
        assert job("fern")["if"].startswith("!cancelled() && ")
        assert "contains(needs.*.result, 'cancelled')" in job("alert")["if"]

    def test_claude_steps_cannot_print_tool_results(self) -> None:
        for step in claude_steps().values():
            assert step["env"] == {"ACTIONS_STEP_DEBUG": "false"}
            assert (
                step["with"]["show_full_output"],
                step["with"]["display_report"],
            ) == (
                "false",
                "false",
            )

    @pytest.mark.parametrize(
        ("name", "tools", "flags"),
        [
            ("holdbacks", "Read,Glob,Grep", []),
            (
                "fern",
                "Read,Glob,Grep,Edit,Write",
                ["--permission-mode", "acceptEdits", "--permission-prompts", "none"],
            ),
        ],
    )
    def test_claude_flags_are_pinned(
        self, name: str, tools: str, flags: list[str]
    ) -> None:
        args = claude_steps()[name]["with"]["claude_args"].split()
        assert args[args.index("--tools") + 1] == tools
        assert {"--restricted", "--strict-mcp-config", "--json-schema"} <= set(args)
        assert " ".join(flags) in " ".join(args)

    def test_write_token_follows_the_edit_check_and_skips_dry_run(self) -> None:
        write = step_position("fern", has_id("fern-write"))
        assert step_position("fern", has_id("patch")) < write
        assert job("fern")["steps"][write]["if"] == "env.MODE != 'dry-run'"

    def test_every_fern_token_is_narrowed_to_the_fern_repository(self) -> None:
        tokens = [
            step["with"]
            for name in FERN_JOBS
            for step in job(name)["steps"]
            if uses(TOKEN_ACTION)(step)
        ]
        assert len(tokens) == 3
        for inputs in tokens:
            assert (inputs["owner"], inputs["repositories"]) == (
                "${{ env.FERN_OWNER }}",
                "${{ env.FERN_REPO_NAME }}",
            )
            assert any(key.startswith("permission-") for key in inputs)

    def test_fern_facts_match_the_scripts(self, releasedata: ModuleType) -> None:
        env = workflow()["env"]
        assert (
            f"{env['FERN_OWNER']}/{env['FERN_REPO_NAME']}",
            env["FERN_BASE"],
            env["HELD_ITEMS"],
            env["DOCS_STRUCTURE"],
        ) == (
            releasedata.FERN_REPO,
            releasedata.FERN_BASE,
            releasedata.HELD_ITEMS,
            releasedata.DOCS_STRUCTURE,
        )

    def test_filing_always_gets_the_held_items(self) -> None:
        """Held indexes drop items from the changelog and, even when every
        item is held, keep the docs PR from merging."""
        run = step_run("file")
        held = run.index('--held "${held[@]}"')
        assert held < run.index('if [[ "$HAS_ENTRY" == true ]]')
        assert '--notes "$RUNNER_TEMP/full/release-notes.json"' in run
        (download,) = [
            s
            for s in job("fern")["steps"]
            if s.get("with", {}).get("name") == "release-notes-full"
        ]
        assert download["with"]["path"] == "${{ runner.temp }}/full"

    def test_a_failed_docs_review_still_files_the_changelog_and_fails(
        self,
    ) -> None:
        steps = job("fern")["steps"]
        review = steps[step_position("fern", uses(CLAUDE_ACTION))]
        assert review["continue-on-error"] == "true"
        assert '$PATCH" == success' in step_run("file")
        fail = step_position(
            "fern", lambda s: s.get("name") == "Fail on a failed docs review"
        )
        assert steps[fail]["if"] == (
            "steps.state.outputs.docs != 'skip' && steps.patch.outcome != 'success'"
        )
        assert step_position("fern", has_id("file")) < fail

    def test_alert_covers_holdbacks_and_fern(self) -> None:
        assert {"holdbacks", "fern"} <= needs("alert")

    def test_fern_filings_run_one_at_a_time(self) -> None:
        assert job("fern")["concurrency"] == {
            "group": "fern-python-sdk-${{ inputs.mode == 'dry-run' && github.run_id"
            " || inputs.mode || 'live' }}",
            "cancel-in-progress": "false",
        }
