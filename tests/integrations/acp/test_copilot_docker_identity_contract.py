"""Contract: copilot_docker examples require one shared Band identity.

Room-scoped Band tools execute as the remote band-mcp's ``BAND_AGENT_KEY``.
The host ``client.py`` loads ``copilot_acp_agent`` from ``agent_config.yaml``.
Those must be the same agent — documented in README / ``.env.example``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from dotenv import dotenv_values
from yaml import safe_load

from tests.paths import EXAMPLES_ROOT

ROOT = EXAMPLES_ROOT / "acp" / "copilot_docker"
AGENT_NAME = "copilot_acp_agent"


@dataclass(frozen=True)
class VariantIdentity:
    """Readable projection of one Docker variant's shared-identity contract."""

    env_documents_agent_key: bool
    readme_documents_same_identity: bool
    client_uses_named_agent: bool
    client_rejects_mismatched_key: bool
    client_uses_loaded_identity: bool
    client_uses_remote_mcp: bool


def _observe_variant(variant: str) -> VariantIdentity:
    readme = (ROOT / variant / "README.md").read_text(encoding="utf-8")
    readme = " ".join(readme.replace("**", "").replace("`", "").split())
    env_path = ROOT / variant / ".env.example"
    env_text = env_path.read_text(encoding="utf-8")
    env_values = dotenv_values(env_path)
    client = (ROOT / variant / "client.py").read_text(encoding="utf-8")
    return VariantIdentity(
        env_documents_agent_key=(
            "BAND_AGENT_KEY" in env_values and AGENT_NAME in env_text
        ),
        readme_documents_same_identity=(
            AGENT_NAME in readme
            and "BAND_AGENT_KEY" in readme
            and "agent_config.yaml" in readme
            and "same" in readme.lower()
        ),
        client_uses_named_agent=(f'load_agent_config("{AGENT_NAME}")' in client),
        client_rejects_mismatched_key=("settings.band_agent_key != api_key" in client),
        client_uses_loaded_identity=(
            "agent_id=agent_id" in client and "api_key=api_key" in client
        ),
        client_uses_remote_mcp="inject_band_tools=False" in client,
    )


EXPECTED = VariantIdentity(
    env_documents_agent_key=True,
    readme_documents_same_identity=True,
    client_uses_named_agent=True,
    client_rejects_mismatched_key=True,
    client_uses_loaded_identity=True,
    client_uses_remote_mcp=True,
)


@pytest.mark.parametrize("variant", ["colocated", "compose"])
def test_variant_shares_one_band_identity(variant: str) -> None:
    assert _observe_variant(variant) == EXPECTED


def test_compose_passes_agent_key_to_band_mcp() -> None:
    compose = safe_load(
        (ROOT / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert (
        compose["services"]["band-mcp"]["environment"]["BAND_AGENT_KEY"]
        == "${BAND_AGENT_KEY:?set BAND_AGENT_KEY in .env}"
    )


def test_colocated_launch_passes_agent_key_to_entrypoint() -> None:
    readme = " ".join(
        (ROOT / "colocated" / "README.md")
        .read_text(encoding="utf-8")
        .replace("**", "")
        .replace("`", "")
        .split()
    )
    entrypoint = (ROOT / "colocated" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "docker run --rm --env-file .env" in readme
    assert ': "${BAND_AGENT_KEY:?' in entrypoint
