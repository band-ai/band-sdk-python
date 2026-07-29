"""Fixtures for the bug-hunting skill's script tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from tests.skills.bughunting.scripts import loaded_script


@pytest.fixture
def discovery() -> Iterator[ModuleType]:
    with loaded_script("discover") as module:
        yield module


@pytest.fixture
def runner() -> Iterator[ModuleType]:
    with loaded_script("runner") as module:
        yield module


@pytest.fixture
def plan_repo(tmp_path: Path) -> Path:
    """A throwaway repository holding one example, for plan-validation tests.

    Plans are validated against a repository root, and pinning those tests to a
    real example under ``examples/`` would make an unrelated rename break them.
    """
    example = tmp_path / "examples" / "example.py"
    example.parent.mkdir(parents=True)
    example.write_text("def main() -> None: ...\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def write_plan(tmp_path: Path) -> Callable[[dict[str, object]], Path]:
    """Write a plan document to disk and return its path."""

    def write(document: dict[str, object]) -> Path:
        path = tmp_path / "plan.yaml"
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return path

    return write
