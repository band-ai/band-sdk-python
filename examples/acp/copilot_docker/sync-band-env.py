# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
CLI for :mod:`bandenv` — sync ``BAND_AGENT_KEY`` and optionally start Docker.

Prefer letting the example do this: ``client.py`` calls :func:`bandenv.sync_env`
on startup, and ``--up`` syncs then starts the variant's containers.

From a variant directory (after ``cp .env.example .env`` and setting
``GITHUB_TOKEN``)::

    uv run ../sync-band-env.py --up

From the repo root::

    uv run examples/acp/copilot_docker/sync-band-env.py compose --up
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Same directory as bandenv.py (uv run may not put the script dir on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bandenv import resolve_variant_dir, sync_env

logger = logging.getLogger(__name__)


def _start_docker(variant_dir: Path) -> None:
    name = variant_dir.name
    if name == "compose":
        cmd = ["docker", "compose", "up", "--build"]
    elif name == "colocated":
        cmd = [
            "docker",
            "build",
            "-t",
            "copilot-band-acp",
            ".",
        ]
        subprocess.run(cmd, cwd=variant_dir, check=True)
        cmd = [
            "docker",
            "run",
            "--rm",
            "--env-file",
            ".env",
            "-p",
            "127.0.0.1:8080:8080",
            "copilot-band-acp",
        ]
    else:
        raise ValueError(f"unknown variant directory {variant_dir}")
    logger.info("Starting Docker: %s (cwd=%s)", " ".join(cmd), variant_dir)
    subprocess.run(cmd, cwd=variant_dir, check=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Sync BAND_AGENT_KEY from agent_config.yaml; optionally start Docker."
    )
    parser.add_argument(
        "variant",
        nargs="?",
        choices=("colocated", "compose"),
        help="colocated or compose (optional if cwd is that directory)",
    )
    parser.add_argument(
        "--up",
        action="store_true",
        help="after syncing .env, build/start the variant's Docker stack",
    )
    args = parser.parse_args()

    variant_dir = resolve_variant_dir(args.variant)
    sync_env(variant_dir)
    if args.up:
        _start_docker(variant_dir)


if __name__ == "__main__":
    main()
