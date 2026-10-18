from __future__ import annotations

from tests.framework_conformance import venv_job_coverage as vjc

_PARLANT_ONLY_MODULES = {
    "parlant": "parlant",
    "werkzeug": "werkzeug",
}


def _job_command() -> str:
    return vjc.job_command("test-parlant", "Run parlant tests")


def activity_command() -> str:
    return vjc.job_command("test-parlant", "Run Parlant activity tests")
