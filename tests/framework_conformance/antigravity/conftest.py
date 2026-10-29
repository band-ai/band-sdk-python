"""Disposable native executable discovery fixtures."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture
def native_executable(tmp_path: Path) -> Path:
    directory = tmp_path / "native installation with spaces"
    directory.mkdir()
    executable = directory / Path(sys.executable).name
    shutil.copy2(sys.executable, executable)
    return executable
