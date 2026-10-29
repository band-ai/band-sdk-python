"""Load the shipped installer without depending on the optional ACP extra."""

from __future__ import annotations

import importlib.util
from types import ModuleType

import pytest

from tests.paths import CI_SCRIPTS


@pytest.fixture
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "setup_antigravity", CI_SCRIPTS / "setup-antigravity.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def acp_extra() -> None:
    pytest.importorskip("acp", reason="native command policy requires the ACP extra")
