"""Share one Band identity between host client.py and in-container band-mcp.

``agent_config.yaml`` → ``copilot_acp_agent`` is the source of truth. band-mcp
only reads ``BAND_AGENT_KEY`` from the variant ``.env``, so :func:`sync_env`
copies that agent's api key into ``.env`` before Docker starts (and again when
``client.py`` boots).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from band.config import load_agent_config

logger = logging.getLogger(__name__)

AGENT_NAME = "copilot_acp_agent"
ENV_KEY = "BAND_AGENT_KEY"
VARIANTS = ("colocated", "compose")
HERE = Path(__file__).resolve().parent


def upsert_env_value(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in a dotenv file, replacing any existing assignment."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    prefix = f"{key}="
    replacement = f"{key}={value}\n"
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(prefix) and not stripped.startswith("#"):
            out.append(replacement)
            replaced = True
        else:
            out.append(line if line.endswith("\n") else f"{line}\n")
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] = f"{out[-1]}\n"
        out.append(replacement)
    path.write_text("".join(out), encoding="utf-8")


def resolve_variant_dir(variant: str | None) -> Path:
    """Resolve a colocated/compose directory from an arg or the process cwd."""
    if variant is not None:
        if variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
        return HERE / variant

    cwd = Path.cwd().resolve()
    if cwd.name in VARIANTS and cwd.parent == HERE:
        return cwd
    raise ValueError(
        "pass a variant (colocated|compose) or run from that variant directory"
    )


def sync_env(variant_dir: Path) -> Path:
    """Ensure ``variant_dir/.env`` has ``BAND_AGENT_KEY`` from agent_config.yaml.

    Creates ``.env`` from ``.env.example`` when missing. Returns the ``.env`` path.
    """
    variant_dir = variant_dir.resolve()
    env_path = variant_dir / ".env"
    example_path = variant_dir / ".env.example"
    if not env_path.exists():
        if not example_path.is_file():
            raise ValueError(f"missing {example_path}; cannot create {env_path}")
        shutil.copy(example_path, env_path)
        logger.info("Created %s from .env.example", env_path)

    _agent_id, api_key = load_agent_config(AGENT_NAME)
    upsert_env_value(env_path, ENV_KEY, api_key)
    logger.info(
        "Synced %s in %s from agent_config.yaml (%s)",
        ENV_KEY,
        env_path,
        AGENT_NAME,
    )
    return env_path
