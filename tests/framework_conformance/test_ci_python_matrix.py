"""Guard: every CI matrix job tests exactly the Python versions pyproject advertises.

An advertised version CI never runs, or a job whose matrix lags the others, is a
support claim nothing backs.
"""

from __future__ import annotations

import re
import tomllib

import yaml

from tests.framework_conformance import venv_job_coverage as vjc
from tests.paths import REPO_ROOT

PYTHON_CLASSIFIER = re.compile(r"Programming Language :: Python :: (3\.\d+)")


def classified_pythons() -> set[str]:
    """Interpreter versions pyproject.toml advertises via trove classifiers."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    matches = (
        PYTHON_CLASSIFIER.fullmatch(classifier)
        for classifier in pyproject["project"]["classifiers"]
    )
    return {match.group(1) for match in matches if match}


def matrix_pythons() -> dict[str, set[str]]:
    """Interpreter versions each ci.yml job that defines a Python matrix runs."""
    workflow = yaml.safe_load(vjc.CI_WORKFLOW.read_text(encoding="utf-8"))
    return {
        name: set(job["strategy"]["matrix"]["python-version"])
        for name, job in workflow["jobs"].items()
        if "python-version" in job.get("strategy", {}).get("matrix", {})
    }


def test_every_matrix_job_tests_exactly_the_classified_pythons() -> None:
    classified = classified_pythons()
    matrices = matrix_pythons()
    assert classified and matrices, "no classifiers or no matrix jobs were found"
    assert matrices == {job: classified for job in matrices}
