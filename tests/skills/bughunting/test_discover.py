"""Tests for the bug-hunting skill's example inventory script."""

from __future__ import annotations

from types import ModuleType

from tests.paths import REPO_ROOT


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
    paths = {item.path for item in discovery.discover(REPO_ROOT, "examples", None)}
    assert "examples/agentcore/agentcore_llm_server.py" in paths
    assert "examples/run_agent.py" in paths


def test_imported_settings_are_reported(discovery: ModuleType) -> None:
    examples = {
        item.path: item
        for item in discovery.discover(REPO_ROOT, "examples", "opencode")
    }
    basic = examples["examples/opencode/01_basic_agent.py"]
    assert basic.config_keys == ("darter",)
    assert {
        "AGENT_KEY",
        "BAND_REST_URL",
        "BAND_WS_URL",
        "OPENCODE_BASE_URL",
    }.issubset(basic.environment)
