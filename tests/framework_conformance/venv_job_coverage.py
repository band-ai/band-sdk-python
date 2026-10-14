"""Coverage of isolated jobs, derived from real consumers and pytest selection."""

from __future__ import annotations

import ast
import json
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from tests.e2e.baseline.toolkit.deps import Extra
from tests.paths import REPO_ROOT


class CoverageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    BAND_TEST_EXTRA: Extra | None = None


CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
_TESTS_ROOT = REPO_ROOT / "tests"

# A `-k` filter anywhere in a job's command, e.g. `tests/framework_conformance/ -k crewai`.
_KEYWORD_FILTER = re.compile(r"(?:^|\s)-k(?:\s|=)")


def needs_venv_pattern(import_names: frozenset[str]) -> re.Pattern[str]:
    """Match a real (non-TYPE_CHECKING) use of any of ``import_names``.

    Filename is not the signal — most tests for an isolated framework fake the
    package through ``sys.modules`` and run fine in `dev`; the miss this guards
    against was a file with no framework name in it at all.
    """
    names = "|".join(sorted(import_names))
    return re.compile(
        rf"^\s*(?:import|from)\s+(?:{names})\b|importorskip\(\s*[\"'](?:{names})",
        re.MULTILINE,
    )


def only_distributions(extra: str, baseline_extra: str = "dev") -> set[str]:
    """Distributions in ``extra`` that ``baseline_extra`` does not install."""
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["optional-dependencies"]

    def name(requirement: str) -> str:
        return re.split(r"[<>=!\[;]", requirement, maxsplit=1)[0].strip()

    return {name(r) for r in extras[extra]} - {name(r) for r in extras[baseline_extra]}


def consumer_source(path: Path, visited: set[Path] | None = None) -> str:
    """Include same-suite helpers so extracting a probe cannot hide its consumers."""
    seen = visited if visited is not None else set()
    if path in seen:
        return ""
    seen.add(path)
    source = path.read_text(encoding="utf-8")
    helpers = {
        REPO_ROOT.joinpath(*node.module.split(".")).with_suffix(".py")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("tests.")
    }
    return (
        source
        + "\n"
        + "\n".join(
            consumer_source(helper, seen)
            for helper in sorted(helpers)
            if helper.parent == path.parent and helper.exists()
        )
    )


def tests_needing_venv(pattern: re.Pattern[str]) -> set[Path]:
    """Unit-test consumers of a real dependency, including their shared probes."""
    return {
        path.relative_to(REPO_ROOT)
        for path in _TESTS_ROOT.rglob("test_*.py")
        if "e2e" not in path.relative_to(_TESTS_ROOT).parts
        and pattern.search(consumer_source(path))
    }


def job_command(job_name: str, step_name: str) -> str:
    """The shell command of ``step_name`` in ci.yml's ``job_name`` job."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_name]["steps"]
    command = next(step["run"] for step in steps if step.get("name") == step_name)
    return command.replace("\\\n", " ")


def job_paths(command: str) -> set[Path]:
    """The pytest targets named in ``command``."""
    return {Path(token) for token in command.split() if token.startswith("tests/")}


def covered_by(target: Path, listed: set[Path], command: str) -> bool:
    """Whether ``target`` is named outright or sits under a listed directory.

    A directory target only covers what the step actually collects from it: if
    ``command`` narrows with ``-k``, a file that sits under a listed directory but
    does not match the filter runs nowhere despite looking listed.
    """
    if target in listed:
        return True
    return not _KEYWORD_FILTER.search(command) and any(
        parent in listed for parent in target.parents
    )


@dataclass(frozen=True)
class CollectedNode:
    nodeid: str
    frameworks: frozenset[str]
    skipped: bool


def collected_nodes(arguments: list[str]) -> tuple[CollectedNode, ...]:
    """Collect nodes and framework bindings after pytest applies the job selectors."""
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "nodes.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.framework_conformance.venv_job_coverage",
                str(output),
                *arguments,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        return tuple(
            CollectedNode(row["nodeid"], frozenset(row["frameworks"]), row["skipped"])
            for row in json.loads(output.read_text(encoding="utf-8"))
        )


def selected_nodes(arguments: list[str]) -> set[str]:
    """The exact node IDs surviving a job's pytest selectors."""
    return {node.nodeid for node in collected_nodes(arguments)}


def framework_bindings(item: pytest.Item) -> set[str]:
    """Read actual parametrized framework identities, independent of node names."""
    params = getattr(getattr(item, "callspec", None), "params", {})
    return {
        str(value.framework_id) if hasattr(value, "framework_id") else str(value)
        for name, value in params.items()
        if name in {"framework_id", "adapter_id", "adapter_config", "converter_config"}
    }


def collection_row(item: pytest.Item) -> dict[str, object]:
    skipped = item.get_closest_marker("skip") is not None or any(
        marker.args and marker.args[0] is True for marker in item.iter_markers("skipif")
    )
    return {
        "nodeid": item.nodeid,
        "frameworks": sorted(framework_bindings(item)),
        "skipped": skipped,
    }


def pytest_arguments(command: str) -> list[str]:
    """Read selectors after the workflow's pytest executable."""
    tokens = shlex.split(command)
    return tokens[tokens.index("pytest") + 1 :]


class NodeCollector:
    def __init__(self, output: Path) -> None:
        self.output = output

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.output.write_text(
            json.dumps([collection_row(item) for item in session.items]),
            encoding="utf-8",
        )


if __name__ == "__main__":
    raise SystemExit(
        pytest.main(
            [*sys.argv[2:], "--collect-only", "--no-cov"],
            plugins=[NodeCollector(Path(sys.argv[1]))],
        )
    )
