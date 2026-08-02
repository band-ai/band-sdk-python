"""Contract: copilot_docker examples require one shared Band identity.

Room-scoped Band tools execute as the remote band-mcp's ``BAND_AGENT_KEY``.
The host ``client.py`` loads ``copilot_acp_agent`` from ``agent_config.yaml``.
Those must be the same agent — documented in README / ``.env.example``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from dotenv import dotenv_values

from tests.paths import EXAMPLES_ROOT

ROOT = EXAMPLES_ROOT / "acp" / "copilot_docker"
AGENT_NAME = "copilot_acp_agent"


@dataclass(frozen=True)
class VariantIdentity:
    """Readable projection of one Docker variant's shared-identity contract."""

    agent_name: str
    env_documents_agent_key: bool
    readme_documents_same_identity: bool
    client_uses_named_agent: bool
    client_uses_remote_mcp: bool


def _plain(text: str) -> str:
    """Strip light markdown so wrapped / bold / code-span lines still match."""
    return " ".join(text.replace("**", "").replace("`", "").split())


def _observe_variant(variant: str) -> VariantIdentity:
    readme = _plain((ROOT / variant / "README.md").read_text(encoding="utf-8"))
    env_path = ROOT / variant / ".env.example"
    env_text = env_path.read_text(encoding="utf-8")
    env_values = dotenv_values(env_path)
    client = Path(ROOT / variant / "client.py").read_text(encoding="utf-8")
    return VariantIdentity(
        agent_name=AGENT_NAME if AGENT_NAME in client else "",
        env_documents_agent_key=(
            "BAND_AGENT_KEY" in env_values and AGENT_NAME in env_text
        ),
        readme_documents_same_identity=(
            AGENT_NAME in readme
            and "BAND_AGENT_KEY" in readme
            and "agent_config.yaml" in readme
            and "same" in readme.lower()
        ),
        client_uses_named_agent=(
            f'from_config(\n        "{AGENT_NAME}"' in client
            or f'from_config("{AGENT_NAME}"' in client
        ),
        client_uses_remote_mcp="inject_band_tools=False" in client,
    )


EXPECTED = VariantIdentity(
    agent_name=AGENT_NAME,
    env_documents_agent_key=True,
    readme_documents_same_identity=True,
    client_uses_named_agent=True,
    client_uses_remote_mcp=True,
)


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_variant_shares_one_band_identity(variant: str) -> None:
    assert _observe_variant(variant) == EXPECTED
