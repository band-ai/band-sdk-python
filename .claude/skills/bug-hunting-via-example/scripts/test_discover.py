from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def discovery() -> ModuleType:
    path = Path(__file__).with_name("discover.py")
    spec = importlib.util.spec_from_file_location("bug_hunting_discovery", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_multiline_pep723_dependencies(discovery: ModuleType) -> None:
    source = """# /// script
# requires-python = \">=3.11\"
# dependencies = [
#   \"band-sdk[anthropic]\",
#   \"fastapi>=0.110\",
# ]
# ///
"""
    assert discovery.metadata_dependencies(source) == (
        "band-sdk[anthropic]",
        "fastapi>=0.110",
    )


def test_repository_multiline_examples_are_discovered(discovery: ModuleType) -> None:
    repo = Path(__file__).resolve().parents[4]
    paths = {item.path for item in discovery.discover(repo, "examples", None)}
    assert "examples/agentcore/agentcore_llm_server.py" in paths
    assert "examples/run_agent.py" in paths


def test_imported_settings_are_reported(discovery: ModuleType) -> None:
    repo = Path(__file__).resolve().parents[4]
    examples = {
        item.path: item for item in discovery.discover(repo, "examples", "opencode")
    }
    basic = examples["examples/opencode/01_basic_agent.py"]
    assert basic.config_keys == ("darter",)
    assert {
        "AGENT_KEY",
        "BAND_REST_URL",
        "BAND_WS_URL",
        "OPENCODE_BASE_URL",
    }.issubset(basic.environment)
