"""release-announce.yml runs real scripts and reads only the outputs they write.

A drifted output name makes a gate like ``proceed == 'true'`` silently false, so
every release would skip announcing with a green run and no alert.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
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
# Trusted scripts use the skill checkout or the filing job's own source.
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
    def test_reusable_only_with_explicit_contract(self) -> None:
        config = workflow()
        assert set(config["on"]) == {"workflow_call"}
        assert set(config["on"]["workflow_call"]["inputs"]) == {
            "tag",
            "mode",
            "model",
            "app_client_id",
        }
        assert set(config["on"]["workflow_call"]["secrets"]) == {
            "anthropic_api_key",
            "slack_webhook_url",
            "discord_webhook_url",
            "app_private_key",
        }
        assert "concurrency" not in config

    def test_every_script_it_runs_exists(self) -> None:
        scripts = set(SCRIPT_RUN.findall(workflow_text()))
        assert scripts
        assert {
            name for name in scripts if not (RELEASE_DOCS_SCRIPTS / name).is_file()
        } == set()

    @pytest.mark.parametrize(
        ("step", "script"),
        [
            ("scan", "scanreleases"),
            ("collect", "collectprs"),
            ("build", "builditems"),
            ("holdbacks", "holdbacks"),
            ("state", "fernstate"),
            ("fresh", "fernstate"),
            ("patch", "checkpatch"),
            ("file", "filefern"),
        ],
    )
    def test_gates_read_only_outputs_the_script_writes(
        self, step: str, script: str
    ) -> None:
        assert SCRIPT_RUN.findall(step_run(step)) == [f"{script}.py"]
        assert outputs_read(step)
        assert outputs_read(step) <= outputs_written(script)

    def test_scan_and_manual_tags_feed_every_downstream_job(self) -> None:
        assert job("scan")["if"] == "inputs.tag == ''"
        assert job("scan")["outputs"]["tag"] == "${{ steps.scan.outputs.tag }}"
        for name in (
            "collect",
            "changelog",
            "holdbacks",
            "slack",
            "discord",
            "docs",
            "file",
            "alert",
        ):
            assert "scan" in needs(name)
            assert (
                job(name)["env"]["TAG"] == "${{ inputs.tag || needs.scan.outputs.tag }}"
            )
            assert "!cancelled()" in job(name)["if"] or name == "alert"
        assert "needs.scan.outputs.tag != ''" in job("collect")["if"]

    def test_every_checkout_names_its_repository_and_revision(self) -> None:
        checkouts = [
            step["with"] for step in all_steps() if uses("actions/checkout")(step)
        ]
        assert checkouts
        for checkout in checkouts:
            assert "repository" in checkout
            assert "ref" in checkout
            assert (checkout["repository"], checkout["ref"]) in {
                ("${{ job.workflow_repository }}", "${{ job.workflow_sha }}"),
                ("${{ job.workflow_repository }}", "${{ env.TAG }}"),
                ("${{ github.repository }}", "${{ env.FERN_BASE }}"),
            }
            assert checkout.get("sparse-checkout", SKILL_CHECKOUT) == SKILL_CHECKOUT
        fern = [
            checkout
            for checkout in checkouts
            if checkout["repository"] == "${{ github.repository }}"
        ]
        assert [checkout["path"] for checkout in fern] == ["fern", "fern", "fern-write"]
        assert REPO_ROOT / workflow()["env"]["SKILL"] == RELEASE_DOCS_SKILL

    def test_sdk_commands_do_not_inherit_the_fern_repository(self) -> None:
        for step in all_steps():
            if "collectprs.py" in step.get("run", ""):
                assert step["env"]["GH_REPO"] == "${{ job.workflow_repository }}"
        assert "--repo sdk" in step_run("collect")
        assert '--caller "$GITHUB_REPOSITORY" --run-id "$GITHUB_RUN_ID"' in step_run(
            "collect"
        )

    def test_marker_is_live_only_and_last_in_collect(
        self, releasedata: ModuleType
    ) -> None:
        marker = job("collect")["steps"][-1]
        assert uses(UPLOAD_ACTION)(marker)
        assert (
            marker["if"]
            == "steps.collect.outputs.proceed == 'true' && env.MODE == 'live'"
        )
        assert marker["with"] == {
            "name": "${{ env.ANNOUNCED_MARKER_PREFIX }}${{ env.TAG }}",
            "path": "${{ runner.temp }}/marker/tag",
            "retention-days": "${{ env.MARKER_RETENTION_DAYS }}",
            "overwrite": "true",
            "if-no-files-found": "error",
        }
        assert "printf" in job("collect")["steps"][-2]["run"]
        assert releasedata.LOOKBACK_DAYS < releasedata.MARKER_RETENTION_DAYS
        assert "gh release upload" not in workflow_text()

    @pytest.mark.parametrize("name", ["slack", "discord"])
    def test_nothing_is_posted_before_holdbacks(self, name: str) -> None:
        assert {"changelog", "holdbacks"} <= needs(name)
        assert "needs.holdbacks.outputs.has_entry == 'true'" in job(name)["if"]
        post = next(
            step
            for step in job(name)["steps"]
            if step["name"] == f"Post to {name.title()}"
        )
        assert post["if"] == "env.MODE != 'dry-run'"

    def test_announcement_targets_exist(self, announcement: ModuleType) -> None:
        targets = set(re.findall(r"--target ([\w-]+)", workflow_text()))
        assert targets == {target.value for target in announcement.Target}

    @pytest.mark.parametrize(
        ("name", "tools"),
        [
            ("changelog", "Read,Glob,Grep"),
            ("holdbacks", "Read,Glob,Grep"),
            ("docs", "Read,Glob,Grep,Edit,Write"),
        ],
    )
    def test_models_have_restricted_tools_and_explicit_bot_access(
        self, name: str, tools: str
    ) -> None:
        step = job(name)["steps"][step_position(name, uses(CLAUDE_ACTION))]
        args = step["with"]["claude_args"].split()
        assert args[args.index("--tools") + 1] == tools
        assert step_position(
            name, lambda s: "releaseconfig.py" in s.get("run", "")
        ) < step_position(name, uses(CLAUDE_ACTION))
        assert {"--restricted", "--strict-mcp-config", "--json-schema"} <= set(args)
        assert step["with"]["allowed_bots"] == "${{ env.ALLOWED_BOTS }}"
        assert step["with"]["show_full_output"] == "false"
        assert step["with"]["display_report"] == "false"
        assert step["with"]["anthropic_api_key"] == "${{ secrets.anthropic_api_key }}"

    def test_pristine_and_scripts_are_outside_the_review_workspace(self) -> None:
        prepare = job("docs")["steps"][
            step_position("docs", lambda s: s["name"] == "Prepare the review workspace")
        ]
        assert 'cp -a fern "$RUNNER_TEMP/pristine"' in prepare["run"]
        assert 'rm -rf "$RUNNER_TEMP/pristine/.git" fern/.git' in prepare["run"]
        assert prepare["run"].index("cp -a fern") < prepare["run"].index("rm -rf")
        assert step_position(
            "docs", lambda s: s["name"] == "Prepare trusted SDK inputs"
        ) < step_position("docs", uses(CLAUDE_ACTION))
        assert step_position(
            "docs", lambda s: s["name"] == "Prepare the review workspace"
        ) < step_position("docs", uses(CLAUDE_ACTION))
        assert 'cp "$SRC/$SKILL/docs.md" docs.md' in prepare["run"]
        trusted = next(
            step
            for step in job("docs")["steps"]
            if step["name"] == "Prepare trusted SDK inputs"
        )
        assert trusted["env"] == {
            "WORKFLOW_REPOSITORY": "${{ job.workflow_repository }}",
            "WORKFLOW_SHA": "${{ job.workflow_sha }}",
        }
        assert (
            'git clone --quiet "https://github.com/$WORKFLOW_REPOSITORY.git" "$RUNNER_TEMP/source"'
            in trusted["run"]
        )
        assert 'checkout --quiet "$WORKFLOW_SHA"' in trusted["run"]
        schema = job("docs")["steps"][step_position("docs", has_id("docs-schema"))]
        assert schema["if"] == "steps.review-workspace.outcome == 'success'"

    def test_review_artifact_contains_original_edited_and_report(self) -> None:
        package = next(
            step
            for step in job("docs")["steps"]
            if step["name"] == "Package the reviewed trees"
        )
        assert (
            '"$RUNNER_TEMP/pristine" "$RUNNER_TEMP/docs-review/pristine"'
            in package["run"]
        )
        assert 'fern "$RUNNER_TEMP/docs-review/edited"' in package["run"]
        assert (
            '"$RUNNER_TEMP/docs-report.json" "$RUNNER_TEMP/docs-review/docs-report.json"'
            in package["run"]
        )
        upload = job("docs")["steps"][-1]
        assert upload["with"]["name"] == "docs-review"
        assert upload["with"]["include-hidden-files"] == "true"

    def test_filing_rechecks_with_trusted_scripts_before_minting_write_token(
        self,
    ) -> None:
        assert not any(uses(CLAUDE_ACTION)(step) for step in job("file")["steps"])
        patch = step_run("patch")
        assert (
            'uv run --script "$SCRIPTS/checkpatch.py" "$RUNNER_TEMP/docs-review/pristine" "$RUNNER_TEMP/docs-review/edited"'
            in patch
        )
        assert '--out "$RUNNER_TEMP/patch.json" --outputs "$GITHUB_OUTPUT"' in patch
        assert step_position("file", has_id("patch")) < step_position(
            "file", has_id("fern-write")
        )
        assert '--pristine "$RUNNER_TEMP/docs-review/pristine"' in step_run("file")
        assert '--edited "$RUNNER_TEMP/docs-review/edited"' in step_run("file")
        assert '--repo "$SRC"' in step_run("docs-rerange")
        assert (
            step_run("mdx-reader") == 'npm ci --ignore-scripts --prefix "$SCRIPTS/mdx"'
        )

    def test_only_filing_mints_a_narrow_write_token_and_checks_use_builtin_token(
        self,
    ) -> None:
        tokens = [
            (name, step)
            for name, spec in workflow()["jobs"].items()
            for step in spec["steps"]
            if uses(TOKEN_ACTION)(step)
        ]
        assert len(tokens) == 1
        name, token = tokens[0]
        assert name == "file"
        assert token["if"] == "env.MODE != 'dry-run'"
        assert token["with"] == {
            "client-id": "${{ inputs.app_client_id }}",
            "private-key": "${{ secrets.app_private_key }}",
            "owner": "${{ env.FERN_OWNER }}",
            "repositories": "${{ env.FERN_REPO_NAME }}",
            "permission-contents": "write",
            "permission-pull-requests": "write",
        }
        filer = job("file")["steps"][step_position("file", has_id("file"))]
        assert (filer["env"]["GH_TOKEN"], filer["env"]["CHECKS_TOKEN"]) == (
            "${{ steps.fern-write.outputs.token }}",
            "${{ github.token }}",
        )

    def test_per_job_permissions_keep_models_read_only(self) -> None:
        expected = {name: {"contents": "read"} for name in workflow()["jobs"]}
        for name in ("scan", "collect"):
            expected[name] |= {"actions": "read", "pull-requests": "read"}
        expected["docs"]["pull-requests"] = "read"
        expected["file"] |= {"checks": "read", "pull-requests": "read"}
        assert {
            name: spec["permissions"] for name, spec in workflow()["jobs"].items()
        } == expected

    def test_a_failed_docs_review_still_files_changelog_then_fails(self) -> None:
        assert (
            job("file")["if"] == "!cancelled() && needs.holdbacks.result == 'success'"
        )
        for step in job("docs")["steps"]:
            if step.get("id") in {
                "docs-range",
                "review-all",
                "review-input",
                "docs-claude",
                "docs-report",
            }:
                assert step["continue-on-error"] == "true"
        assert '$PATCH" == success' in step_run("file")
        assert '--held "${held[@]}"' in step_run("file")
        assert step_run("file").index("--held") < step_run("file").index(
            'if [[ "$HAS_ENTRY"'
        )
        fail = step_position(
            "file", lambda step: step["name"] == "Fail on a failed docs review"
        )
        assert step_position("file", has_id("file")) < fail
        assert (
            "needs.docs.outputs.review != 'success'" in job("file")["steps"][fail]["if"]
        )
        assert {"scan", "holdbacks", "docs", "file"} <= needs("alert")
        assert "contains(needs.*.result, 'cancelled')" in job("alert")["if"]

    def test_filing_group_cannot_collide_with_caller(self) -> None:
        assert job("file")["concurrency"]["group"].startswith("release-docs-filing-")
        assert job("file")["concurrency"]["cancel-in-progress"] == "false"
