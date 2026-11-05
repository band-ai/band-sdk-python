"""Evaluate redacted operator evidence without treating offline tests as live proof."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from bandteams.evidence import Verdict
from bandteams.settings import StrictModel

PROBES = tuple(f"Q{number}" for number in range(1, 10))
ENVIRONMENTS = ("local-tunnel", "stable-server")


class Result(StrictModel):
    environment: Literal["local-tunnel", "stable-server"]
    probe: str = Field(pattern=r"^Q[1-9]$")
    verdict: Verdict
    artifacts: tuple[str, ...] = ()
    external_incompatibility: bool = False


class Evidence(StrictModel):
    run_id: str = Field(min_length=1)
    code_revision: str = Field(min_length=1)
    image_digest: str = Field(min_length=1)
    cli_version: Literal["3.0.4"]
    client_version: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    aliases: tuple[str, ...] = Field(min_length=2)
    independent_admission_artifacts: tuple[str, ...] = ()
    results: tuple[Result, ...]

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        keys = {(result.environment, result.probe) for result in self.results}
        if len(keys) != len(self.results) or len(set(self.aliases)) != len(
            self.aliases
        ):
            raise ValueError("Evidence cases and user aliases must be unique")
        return self


def decision(evidence: Evidence, directory: Path) -> str:
    """Require complete same-run live evidence for both deployment routes."""

    def artifacts_exist(artifacts: tuple[str, ...]) -> bool:
        return bool(artifacts) and all(
            (directory / artifact).is_file() for artifact in artifacts
        )

    if any(
        result.verdict == Verdict.FAIL
        and result.external_incompatibility
        and artifacts_exist(result.artifacts)
        for result in evidence.results
    ):
        return "NO-GO"
    expected = {
        (environment, probe) for environment in ENVIRONMENTS for probe in PROBES
    }
    passed = {
        (result.environment, result.probe)
        for result in evidence.results
        if result.verdict == Verdict.PASS and artifacts_exist(result.artifacts)
    }
    if expected == passed and artifacts_exist(evidence.independent_admission_artifacts):
        return "GO"
    return "BLOCKED"
