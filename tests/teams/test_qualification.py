"""Keep qualification blocked when evidence is missing or a harness fails."""

from __future__ import annotations

from typing import Any

import pytest
from bandteams.evidence import Verdict
from bandteams.qualification import ENVIRONMENTS, PROBES, Evidence, Result, decision


class TestQualification:
    @pytest.mark.parametrize(
        "complete,admission,expected",
        [(False, True, "BLOCKED"), (True, False, "BLOCKED"), (True, True, "GO")],
    )
    def test_both_environments_and_independent_admission_are_required(
        self, tmp_path: Any, complete: Any, admission: Any, expected: Any
    ) -> None:
        artifact = tmp_path / "redacted.json"
        artifact.write_text("{}")
        environments = ENVIRONMENTS if complete else ENVIRONMENTS[:1]
        evidence = Evidence(
            run_id="run",
            code_revision="revision",
            image_digest="digest",
            cli_version="3.0.4",
            client_version="client",
            timestamp="timestamp",
            aliases=("alice", "bob"),
            independent_admission_artifacts=("redacted.json",) if admission else (),
            results=tuple(
                Result(
                    environment=environment,
                    probe=probe,
                    verdict=Verdict.PASS,
                    artifacts=("redacted.json",),
                )
                for environment in environments
                for probe in PROBES
            ),
        )
        assert decision(evidence, tmp_path) == expected

    @pytest.mark.parametrize("external,expected", [(False, "BLOCKED"), (True, "NO-GO")])
    def test_harness_failure_is_not_external_no_go(
        self, tmp_path: Any, external: Any, expected: Any
    ) -> None:
        (tmp_path / "redacted.json").write_text("{}")
        evidence = Evidence(
            run_id="run",
            code_revision="revision",
            image_digest="digest",
            cli_version="3.0.4",
            client_version="client",
            timestamp="timestamp",
            aliases=("alice", "bob"),
            results=(
                Result(
                    environment="stable-server",
                    probe="Q1",
                    verdict=Verdict.FAIL,
                    artifacts=("redacted.json",),
                    external_incompatibility=external,
                ),
            ),
        )
        assert decision(evidence, tmp_path) == expected
