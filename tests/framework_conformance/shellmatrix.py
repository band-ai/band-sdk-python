"""Select an explicit shell-startup hypothesis for isolated hosted probes."""

from __future__ import annotations

import subprocess
from enum import StrEnum
from typing import Any

import pytest

NATIVE_RUN = subprocess.run


class ShellMode(StrEnum):
    BASELINE = "baseline"
    CLOSED_STDIN = "closed-stdin"
    NONINTERACTIVE = "noninteractive"
    BOTH = "both"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shell-probe-mode", choices=list(ShellMode), default=ShellMode.BASELINE
    )


@pytest.fixture(autouse=True)
def shell_hypothesis(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mode = ShellMode(request.config.getoption("--shell-probe-mode"))
    if mode is ShellMode.BASELINE:
        return

    def run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if isinstance(args, list) and args[0] == "powershell":
            args = args.copy()
            if mode in {ShellMode.NONINTERACTIVE, ShellMode.BOTH}:
                args.insert(1, "-NonInteractive")
            if mode in {ShellMode.CLOSED_STDIN, ShellMode.BOTH}:
                kwargs["stdin"] = subprocess.DEVNULL
        return NATIVE_RUN(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
