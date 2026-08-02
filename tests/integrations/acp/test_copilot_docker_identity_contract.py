"""Contract: copilot_docker examples share one Band identity via agent_config.

Room-scoped Band tools execute as the remote band-mcp's ``BAND_AGENT_KEY``.
The host ``client.py`` loads ``copilot_acp_agent`` from ``agent_config.yaml``.
``bandenv.sync_env`` (used by ``sync-band-env.py --up`` and both ``client.py``
files) injects that same key into the variant ``.env``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.loaders import load_script_module
from tests.paths import EXAMPLES_ROOT

ROOT = EXAMPLES_ROOT / "acp" / "copilot_docker"
AGENT_NAME = "copilot_acp_agent"

bandenv = load_script_module(ROOT / "bandenv.py", "copilot_docker_bandenv")


def _plain(text: str) -> str:
    """Strip light markdown so wrapped / bold / code-span lines still match."""
    return " ".join(text.replace("**", "").replace("`", "").split())


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_readme_documents_auto_sync(variant: str) -> None:
    readme = (ROOT / variant / "README.md").read_text(encoding="utf-8")
    plain = _plain(readme)
    assert "sync-band-env.py --up" in plain
    assert AGENT_NAME in readme
    assert "agent_config.yaml" in plain
    assert "client.py" in plain


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_env_example_points_at_auto_sync(variant: str) -> None:
    env_path = ROOT / variant / ".env.example"
    text = env_path.read_text(encoding="utf-8")
    values = dotenv_values(env_path)
    assert "BAND_AGENT_KEY" in values
    assert "client.py" in text
    assert AGENT_NAME in text


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_client_syncs_env_and_uses_remote_mcp(variant: str) -> None:
    client = Path(ROOT / variant / "client.py").read_text(encoding="utf-8")
    assert "sync_env" in client
    assert f'from_config(\n        "{AGENT_NAME}"' in client or (
        f'from_config("{AGENT_NAME}"' in client
    )
    assert "inject_band_tools=False" in client


def test_bandenv_targets_copilot_acp_agent() -> None:
    assert bandenv.AGENT_NAME == AGENT_NAME
    assert bandenv.ENV_KEY == "BAND_AGENT_KEY"


def test_upsert_env_value_replaces_existing_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GITHUB_TOKEN=gh\nBAND_AGENT_KEY=old\n", encoding="utf-8")
    bandenv.upsert_env_value(env_path, "BAND_AGENT_KEY", "new-key")
    values = dotenv_values(env_path)
    assert values["GITHUB_TOKEN"] == "gh"
    assert values["BAND_AGENT_KEY"] == "new-key"


def test_upsert_env_value_appends_when_missing(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GITHUB_TOKEN=gh\n", encoding="utf-8")
    bandenv.upsert_env_value(env_path, "BAND_AGENT_KEY", "new-key")
    values = dotenv_values(env_path)
    assert values["BAND_AGENT_KEY"] == "new-key"
