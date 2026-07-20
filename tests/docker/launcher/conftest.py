"""Fixtures for launcher unit tests.

Scrubs every supported launcher environment variable so a developer's shell
(or .env.test in full-suite runs) can never leak values into LauncherEnv,
and pins the apparent uid to the agent uid the launcher requires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from band.docker.launcher import AGENT_UID, LauncherEnv
from band.docker.launcher import run as launcher_run

from .fakes import Workspace, make_workspace

SCRUBBED_ENV_VARS = [name.upper() for name in LauncherEnv.model_fields]


@pytest.fixture(autouse=True)
def scrub_launcher_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SCRUBBED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def as_agent_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher_run, "current_uid", lambda: AGENT_UID)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return make_workspace(tmp_path)
