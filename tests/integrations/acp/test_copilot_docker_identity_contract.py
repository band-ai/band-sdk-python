"""Contract: copilot_docker examples require one shared Band identity.

Room-scoped Band tools execute as the remote band-mcp's ``BAND_AGENT_KEY``.
The host ``client.py`` loads ``copilot_acp_agent`` from ``agent_config.yaml``.
Those must be the same agent — documented in README / ``.env.example``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.paths import EXAMPLES_ROOT

ROOT = EXAMPLES_ROOT / "acp" / "copilot_docker"
AGENT_NAME = "copilot_acp_agent"


def _plain(text: str) -> str:
    """Strip light markdown so wrapped / bold / code-span lines still match."""
    return " ".join(text.replace("**", "").replace("`", "").split())


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_readme_documents_shared_identity(variant: str) -> None:
    readme = (ROOT / variant / "README.md").read_text(encoding="utf-8")
    plain = _plain(readme)
    assert AGENT_NAME in readme
    assert "BAND_AGENT_KEY" in plain
    assert "agent_config.yaml" in plain
    assert "same" in plain.lower()


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_env_example_documents_shared_identity(variant: str) -> None:
    env_path = ROOT / variant / ".env.example"
    text = env_path.read_text(encoding="utf-8")
    values = dotenv_values(env_path)
    assert "BAND_AGENT_KEY" in values
    assert AGENT_NAME in text


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_client_uses_remote_mcp_and_named_agent(variant: str) -> None:
    client = Path(ROOT / variant / "client.py").read_text(encoding="utf-8")
    assert f'from_config(\n        "{AGENT_NAME}"' in client or (
        f'from_config("{AGENT_NAME}"' in client
    )
    assert "inject_band_tools=False" in client
    assert "sync_env" not in client
