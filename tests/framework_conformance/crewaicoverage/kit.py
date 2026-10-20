from __future__ import annotations

from tests.framework_conformance import venv_job_coverage as vjc

_CREWAI_ONLY_MODULES = {
    "crewai": "crewai",
    "nest-asyncio": "nest_asyncio",
    "pillow": "PIL",
}


def _job_command() -> str:
    return vjc.job_command("test-crewai", "Run crewai tests")


def activity_command() -> str:
    return vjc.job_command("test-crewai", "Run CrewAI activity tests")
