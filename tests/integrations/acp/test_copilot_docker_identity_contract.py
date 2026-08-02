"""Contract: copilot_docker examples share one Band identity via agent_config.

Room-scoped Band tools execute as the remote band-mcp's ``BAND_AGENT_KEY``.
The host ``client.py`` loads ``copilot_acp_agent`` from ``agent_config.yaml``.
``sync-band-env.py`` injects that same key into the variant ``.env`` so Docker
does not take a second, independent agent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.loaders import load_script_module
from tests.paths import EXAMPLES_ROOT

ROOT = EXAMPLES_ROOT / "acp" / "copilot_docker"
SYNC_SCRIPT = ROOT / "sync-band-env.py"
AGENT_NAME = "copilot_acp_agent"

sync_band_env = load_script_module(SYNC_SCRIPT, "copilot_docker_sync_band_env")


def _plain(text: str) -> str:
    """Strip light markdown so wrapped / bold / code-span lines still match."""
    return " ".join(text.replace("**", "").replace("`", "").split())


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_readme_documents_sync_from_agent_config(variant: str) -> None:
    readme = (ROOT / variant / "README.md").read_text(encoding="utf-8")
    plain = _plain(readme)
    assert "sync-band-env.py" in plain
    assert AGENT_NAME in readme
    assert "404" in readme
    assert "agent_config.yaml" in plain


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_env_example_points_at_sync_script(variant: str) -> None:
    env_path = ROOT / variant / ".env.example"
    text = env_path.read_text(encoding="utf-8")
    values = dotenv_values(env_path)
    assert "BAND_AGENT_KEY" in values
    assert "sync-band-env.py" in text
    assert AGENT_NAME in text


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_client_uses_remote_mcp_topology(variant: str) -> None:
    client = Path(ROOT / variant / "client.py").read_text(encoding="utf-8")
    assert f'from_config(\n        "{AGENT_NAME}"' in client or (
        f'from_config("{AGENT_NAME}"' in client
    )
    assert "inject_band_tools=False" in client


def test_sync_script_loads_copilot_acp_agent() -> None:
    assert sync_band_env.AGENT_NAME == AGENT_NAME
    assert sync_band_env.ENV_KEY == "BAND_AGENT_KEY"


def test_upsert_env_value_replaces_existing_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GITHUB_TOKEN=gh\nBAND_AGENT_KEY=old\n", encoding="utf-8")
    sync_band_env._upsert_env_value(env_path, "BAND_AGENT_KEY", "new-key")
    values = dotenv_values(env_path)
    assert values["GITHUB_TOKEN"] == "gh"
    assert values["BAND_AGENT_KEY"] == "new-key"


def test_upsert_env_value_appends_when_missing(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GITHUB_TOKEN=gh\n", encoding="utf-8")
    sync_band_env._upsert_env_value(env_path, "BAND_AGENT_KEY", "new-key")
    values = dotenv_values(env_path)
    assert values["BAND_AGENT_KEY"] == "new-key"
