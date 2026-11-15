"""Core consumer coverage build and reporting contracts."""

from pathlib import Path

from tests.corecoverage.kit import (
    _run_pin_guard,
    _run_step,
    _step,
    _step_names,
    load_workflow,
)


class TestCoreCoverageWorkflow:
    def test_pin_step_resolves_version_into_github_output(self) -> None:
        step = _step(load_workflow(), "Resolve pinned band-sdk-core version")
        assert step["id"] == "pin"
        assert "importlib.metadata.version('band-sdk-core')" in step["run"]
        assert '>> "$GITHUB_OUTPUT"' in step["run"]
        assert "version=$version" in step["run"]

    def test_pin_guard_passes_for_a_stable_version(self) -> None:
        result = _run_pin_guard("2.4.0")
        assert result.returncode == 0, result.stderr

    def test_pin_guard_rejects_dev_prerelease_versions(self) -> None:
        result = _run_pin_guard("2.4.0.dev3")
        assert result.returncode == 1
        assert "band-sdk-core==2.4.0.dev3 is a pre-release" in result.stderr
        assert "band-sdk-core-core-v2.4.0.dev3" in result.stderr

    def test_pin_guard_rejects_cargo_style_dev_versions(self) -> None:
        result = _run_pin_guard("2.4.0-dev.3")
        assert result.returncode == 1
        assert "band-sdk-core==2.4.0-dev.3 is a pre-release" in result.stderr

    def test_checkout_ref_matches_pin_step_output(self) -> None:
        step = _step(load_workflow(), "Checkout band-sdk-core at the pinned version")
        assert (
            step["with"]["ref"]
            == "${{ env.CORE_TAG_PREFIX }}${{ steps.pin.outputs.version }}"
        )

    def test_core_checkout_uses_the_scoped_read_secret(self) -> None:
        workflow = load_workflow()
        checkout = _step(workflow, "Checkout band-sdk-core at the pinned version")
        names = _step_names(workflow)

        assert checkout["with"]["token"] == "${{ secrets.CORE_SDK_READ_KEY }}"
        assert "Generate GitHub App Token (scoped to band-sdk-core)" not in names

    def test_prerelease_guard_runs_before_the_cross_repo_checkout(self) -> None:
        names = _step_names(load_workflow())
        assert names.index("Resolve pinned band-sdk-core version") < names.index(
            "Checkout band-sdk-core at the pinned version"
        )

    def test_core_tag_prefix_is_a_single_source_of_truth(self) -> None:
        workflow = load_workflow()
        prefix = workflow["jobs"]["coverage"]["env"]["CORE_TAG_PREFIX"]
        assert prefix == "band-sdk-core-core-v"
        checkout_ref = _step(workflow, "Checkout band-sdk-core at the pinned version")[
            "with"
        ]["ref"]
        pin_run = _step(workflow, "Resolve pinned band-sdk-core version")["run"]
        assert "${{ env.CORE_TAG_PREFIX }}" in checkout_ref
        assert "os.environ['CORE_TAG_PREFIX']" in pin_run
        assert "band-sdk-core-core-v" not in checkout_ref
        assert "band-sdk-core-core-v" not in pin_run

    def test_report_dir_and_artifact_dir_are_single_sources_of_truth(self) -> None:
        workflow = load_workflow()
        report_dir = workflow["jobs"]["coverage"]["env"]["REPORT_DIR"]
        artifact_dir = workflow["jobs"]["coverage"]["env"]["ARTIFACT_DIR"]
        summary_run = _step(workflow, "Write coverage summary")["run"]
        stage_run = _step(workflow, "Stage coverage report")["run"]
        upload_paths = _step(workflow, "Upload coverage report")["with"]["path"]
        assert "$REPORT_DIR" in summary_run
        assert "$REPORT_DIR" in stage_run
        assert "$ARTIFACT_DIR" in stage_run
        assert upload_paths == "${{ env.ARTIFACT_DIR }}"
        assert report_dir not in summary_run
        assert report_dir not in stage_run
        assert artifact_dir not in stage_run
        assert artifact_dir not in upload_paths

    def test_stage_coverage_report_copies_only_files_that_exist(
        self, tmp_path: Path
    ) -> None:
        report = tmp_path / "band-sdk-core" / "report"
        report.mkdir(parents=True)
        (report / "python-consumer.lcov").write_text("lcov data")
        (report / "summary.txt").write_text("summary data")
        (report / "html").mkdir()
        (report / "html" / "index.html").write_text("<html></html>")

        result = _run_step(
            "Stage coverage report",
            cwd=tmp_path,
            env={"REPORT_DIR": "report", "ARTIFACT_DIR": "artifact"},
        )

        assert result.returncode == 0, result.stderr
        staged = tmp_path / "artifact"
        assert (staged / "python-consumer.lcov").read_text() == "lcov data"
        assert (staged / "summary.txt").read_text() == "summary data"
        assert (staged / "html" / "index.html").is_file()

    def test_stage_coverage_report_tolerates_a_missing_checkout(
        self, tmp_path: Path
    ) -> None:
        # band-sdk-core/ may not exist at all (see test_write_coverage_summary_has_no_working_directory) --
        # staging must not fail just because there is nothing to stage.
        result = _run_step(
            "Stage coverage report",
            cwd=tmp_path,
            env={"REPORT_DIR": "report", "ARTIFACT_DIR": "artifact"},
        )

        assert result.returncode == 0, result.stderr
        assert list((tmp_path / "artifact").iterdir()) == []

    def test_write_coverage_summary_has_no_working_directory(self) -> None:
        # band-sdk-core/ may not exist (the pin step's guard, or any earlier
        # failure, can skip its checkout) -- a nonexistent step cwd fails the
        # step outright before its own else-branch fallback can run.
        step = _step(load_workflow(), "Write coverage summary")
        assert "working-directory" not in step
        assert step["run"].strip().startswith('summary="band-sdk-core/')

    def test_write_coverage_summary_falls_back_when_missing(self) -> None:
        run_text = _step(load_workflow(), "Write coverage summary")["run"]
        assert "No coverage summary produced" in run_text
        assert '>> "$GITHUB_STEP_SUMMARY"' in run_text

    def test_download_artifact_uses_only_supported_inputs(self) -> None:
        step = _step(load_workflow(), "Download coverage report", job="report-weekly")
        assert "if-no-files-found" not in step["with"]

    def test_weekly_report_is_scheduled_and_mentions_the_integrations_roster(
        self,
    ) -> None:
        workflow = load_workflow()
        assert workflow["on"]["schedule"] == [{"cron": "33 20 * * 0"}]

        report = workflow["jobs"]["report-weekly"]
        assert (
            report["if"]
            == "!cancelled() && (github.event_name == 'schedule' || github.event_name == 'workflow_dispatch')"
        )
        assert report["permissions"] == {"contents": "write"}
        mention_step = _step(
            workflow, "Read integrations mentions list", job="report-weekly"
        )
        digest_step = _step(
            workflow, "Post the weekly coverage digest", job="report-weekly"
        )
        assert mention_step["id"] == "mentions"
        assert mention_step["if"] == "github.event_name == 'schedule'"
        assert (
            digest_step["env"]["RECIPIENTS"]
            == "${{ github.event_name == 'schedule' && steps.mentions.outputs.mentions || format('@{0}', github.triggering_actor) }}"
        )
