"""Contract: copilot_docker examples require a shared host / band-mcp identity.

Room-scoped Band tools execute as the remote band-mcp's ``BAND_AGENT_KEY``.
The host ``client.py`` loads ``copilot_acp_agent`` separately — those two must
be the same Band agent, or room tools 404 while text relay still works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.paths import EXAMPLES_ROOT

ROOT = EXAMPLES_ROOT / "acp" / "copilot_docker"


def _plain(text: str) -> str:
    """Strip light markdown so wrapped / bold / code-span lines still match."""
    return " ".join(text.replace("**", "").replace("`", "").split())


# Stable intent the READMEs must keep (markdown formatting allowed).
SAME_AGENT_PHRASE = (
    "BAND_AGENT_KEY must be the API key of the same Band agent configured as"
)


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_readme_requires_same_band_identity(variant: str) -> None:
    readme = (ROOT / variant / "README.md").read_text(encoding="utf-8")
    assert SAME_AGENT_PHRASE in _plain(readme)
    assert "copilot_acp_agent" in readme
    assert "404" in readme


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_env_example_documents_same_agent_key(variant: str) -> None:
    env_path = ROOT / variant / ".env.example"
    text = env_path.read_text(encoding="utf-8")
    values = dotenv_values(env_path)
    assert "BAND_AGENT_KEY" in values
    assert "same Band agent configured as copilot_acp_agent" in text


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_client_uses_remote_mcp_topology(variant: str) -> None:
    client = Path(ROOT / variant / "client.py").read_text(encoding="utf-8")
    assert 'from_config(\n        "copilot_acp_agent"' in client or (
        'from_config("copilot_acp_agent"' in client
    )
    assert "inject_band_tools=False" in client
