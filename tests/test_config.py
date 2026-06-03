"""
Config loading tests - verify agent configuration management.

Tests cover all error paths and edge cases for loading agent credentials
from YAML configuration files.
"""

from __future__ import annotations

import pytest
from band.config import load_agent_config


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Provide a writable config path wired into the loader."""
    path = tmp_path / "agent_config.yaml"
    monkeypatch.setattr("band.config.loader.get_config_path", lambda: path)
    return path


def test_load_valid_config_success(config_file):
    """Should successfully load agent credentials from valid config."""
    config_file.write_text("""
simple_agent:
  agent_id: test-agent-123
  api_key: test-key-abc
    """)

    agent_id, api_key = load_agent_config("simple_agent")

    assert agent_id == "test-agent-123"
    assert api_key == "test-key-abc"


def test_missing_config_file(tmp_path, monkeypatch):
    """Should raise FileNotFoundError with helpful message when config missing."""
    non_existent = tmp_path / "does_not_exist.yaml"
    monkeypatch.setattr("band.config.loader.get_config_path", lambda: non_existent)

    with pytest.raises(FileNotFoundError) as exc_info:
        load_agent_config("any_agent")

    assert "Config file not found" in str(exc_info.value)
    assert "agent_config.yaml.example" in str(exc_info.value)


def test_agent_key_not_in_config(config_file):
    """Should raise ValueError when requested agent key doesn't exist."""
    config_file.write_text("""
simple_agent:
  agent_id: test-agent-123
  api_key: test-key-abc
    """)

    with pytest.raises(ValueError) as exc_info:
        load_agent_config("nonexistent_agent")

    assert "nonexistent_agent" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_missing_agent_id_field(config_file):
    """Should raise ValueError when agent_id field is missing."""
    config_file.write_text("""
simple_agent:
  api_key: test-key-abc
    """)

    with pytest.raises(ValueError) as exc_info:
        load_agent_config("simple_agent")

    assert "agent_id" in str(exc_info.value)
    assert "Missing required fields" in str(exc_info.value)


def test_missing_api_key_field(config_file):
    """Should raise ValueError when api_key field is missing."""
    config_file.write_text("""
simple_agent:
  agent_id: test-agent-123
    """)

    with pytest.raises(ValueError) as exc_info:
        load_agent_config("simple_agent")

    assert "api_key" in str(exc_info.value)
    assert "Missing required fields" in str(exc_info.value)


def test_missing_both_fields(config_file):
    """Should raise ValueError listing both missing fields."""
    config_file.write_text("""
simple_agent:
  some_other_field: value
    """)

    with pytest.raises(ValueError) as exc_info:
        load_agent_config("simple_agent")

    error_msg = str(exc_info.value)
    assert "agent_id" in error_msg
    assert "api_key" in error_msg
    assert "Missing required fields" in error_msg


def test_empty_agent_id(config_file):
    """Should raise ValueError when agent_id is empty string."""
    config_file.write_text("""
simple_agent:
  agent_id: ""
  api_key: test-key-abc
    """)

    with pytest.raises(ValueError) as exc_info:
        load_agent_config("simple_agent")

    assert "agent_id" in str(exc_info.value)
    assert "Missing required fields" in str(exc_info.value)


def test_empty_api_key(config_file):
    """Should raise ValueError when api_key is empty string."""
    config_file.write_text("""
simple_agent:
  agent_id: test-agent-123
  api_key: ""
    """)

    with pytest.raises(ValueError) as exc_info:
        load_agent_config("simple_agent")

    assert "api_key" in str(exc_info.value)
    assert "Missing required fields" in str(exc_info.value)


def test_empty_yaml_file(config_file):
    """Should raise ValueError when YAML file is empty."""
    config_file.write_text("")

    with pytest.raises(ValueError) as exc_info:
        load_agent_config("simple_agent")

    assert "simple_agent" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_invalid_yaml_syntax(config_file):
    """Should raise RuntimeError when YAML is malformed."""
    config_file.write_text("""
simple_agent:
  agent_id: test-agent-123
  api_key: [unclosed bracket
    """)

    with pytest.raises(RuntimeError) as exc_info:
        load_agent_config("simple_agent")

    assert "Error loading agent config" in str(exc_info.value)


def test_load_multiple_agents_from_same_file(config_file):
    """Should successfully load different agents from same config file."""
    config_file.write_text("""
agent_one:
  agent_id: agent-1-id
  api_key: agent-1-key

agent_two:
  agent_id: agent-2-id
  api_key: agent-2-key
    """)

    agent_id_1, api_key_1 = load_agent_config("agent_one")
    assert agent_id_1 == "agent-1-id"
    assert api_key_1 == "agent-1-key"

    agent_id_2, api_key_2 = load_agent_config("agent_two")
    assert agent_id_2 == "agent-2-id"
    assert api_key_2 == "agent-2-key"


def test_config_with_extra_fields_ignored(config_file):
    """Should ignore extra fields and only return agent_id and api_key."""
    config_file.write_text("""
simple_agent:
  agent_id: test-agent-123
  api_key: test-key-abc
  description: "This is a test agent"
  extra_field: "should be ignored"
  version: 2
    """)

    agent_id, api_key = load_agent_config("simple_agent")

    assert agent_id == "test-agent-123"
    assert api_key == "test-key-abc"
    assert isinstance((agent_id, api_key), tuple)
    assert len((agent_id, api_key)) == 2


def test_load_with_explicit_config_path(tmp_path):
    """Should load keyed config when config_path is passed explicitly."""
    config_path = tmp_path / "custom_config.yaml"
    config_path.write_text("""
reviewer:
  agent_id: reviewer-123
  api_key: reviewer-key
    """)

    agent_id, api_key = load_agent_config("reviewer", config_path=config_path)

    assert agent_id == "reviewer-123"
    assert api_key == "reviewer-key"


def test_flat_format_fallback_with_explicit_config_path(tmp_path):
    """Should fall back to flat YAML format when agent key is absent."""
    config_path = tmp_path / "single_agent.yaml"
    config_path.write_text("""
agent_id: flat-agent-123
api_key: flat-key-abc
role: planner
    """)

    agent_id, api_key = load_agent_config("any_key", config_path=config_path)

    assert agent_id == "flat-agent-123"
    assert api_key == "flat-key-abc"
