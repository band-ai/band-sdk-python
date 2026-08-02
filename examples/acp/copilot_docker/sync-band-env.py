# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Inject the host agent's API key into a copilot_docker variant ``.env``.

Single source of truth: ``copilot_acp_agent`` in ``agent_config.yaml`` (the same
entry ``client.py`` loads). band-mcp in Docker cannot read that file, so this
script writes its ``api_key`` as ``BAND_AGENT_KEY`` for ``docker compose`` /
``docker run --env-file``.

Run from the repo root:
    uv run examples/acp/copilot_docker/sync-band-env.py compose
    uv run examples/acp/copilot_docker/sync-band-env.py colocated

Or from a variant directory (after ``cp .env.example .env`` and setting
``GITHUB_TOKEN``):
    uv run ../sync-band-env.py
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from band.config import load_agent_config

logger = logging.getLogger(__name__)

AGENT_NAME = "copilot_acp_agent"
ENV_KEY = "BAND_AGENT_KEY"
VARIANTS = ("colocated", "compose")
HERE = Path(__file__).resolve().parent


def _upsert_env_value(path: Path, key: str, value: str) -> None:
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


def _resolve_variant_dir(variant: str | None) -> Path:
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        description=(
            f"Write {ENV_KEY} into a copilot_docker .env from "
            f"agent_config.yaml ({AGENT_NAME})."
        )
    )
    parser.add_argument(
        "variant",
        nargs="?",
        choices=VARIANTS,
        help="colocated or compose (optional if cwd is that directory)",
    )
    args = parser.parse_args()

    variant_dir = _resolve_variant_dir(args.variant)
    env_path = variant_dir / ".env"
    example_path = variant_dir / ".env.example"
    if not env_path.exists():
        if not example_path.is_file():
            raise ValueError(f"missing {example_path}; cannot create {env_path}")
        shutil.copy(example_path, env_path)
        logger.info("Created %s from .env.example", env_path)

    _agent_id, api_key = load_agent_config(AGENT_NAME)
    _upsert_env_value(env_path, ENV_KEY, api_key)
    logger.info(
        "Wrote %s into %s from agent_config.yaml (%s) — same identity client.py uses",
        ENV_KEY,
        env_path,
        AGENT_NAME,
    )


if __name__ == "__main__":
    main()
