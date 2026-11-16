"""The self-registration demo records an agent the provision CLI orphaned."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from band.docker.provision import ORPHANED_AGENT_ID_LINE
from tests.loaders import load_script_module

self_registration = load_script_module(
    "examples/sandbox/self-registration/demo.py", "self_registration_orphan_ledger"
)

RUN_NAME = "band-selfreg-demo-orphan"
SETTINGS = SimpleNamespace(endpoints=SimpleNamespace(rest_url="https://band.test"))


class TestProvisionOrphanLedger:
    @pytest.mark.parametrize(
        ("stderr", "expected_agents"),
        [
            pytest.param(
                f"Error: key not persisted\n{ORPHANED_AGENT_ID_LINE}agent-orphaned\n",
                ["agent-orphaned"],
                id="orphaned",
            ),
            pytest.param("Error: registration refused\n", [], id="nothing-registered"),
        ],
    )
    def test_failed_provision_records_only_a_reported_orphan_for_cleanup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        stderr: str,
        expected_agents: list[str],
    ) -> None:
        monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
        monkeypatch.setattr(
            self_registration.subprocess,
            "run",
            lambda argv, **_: subprocess.CompletedProcess(argv, 1, "", stderr),
        )

        with pytest.raises(RuntimeError, match="band-kit provision failed"):
            self_registration._band_kit_provision(
                name=RUN_NAME, workspace=tmp_path, settings=SETTINGS, create=False
            )

        assert self_registration.recorded_resources(RUN_NAME, "agents") == (
            expected_agents
        )
