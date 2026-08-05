"""
Band agent registration — bundled bootstrap wrapper.

Thin wrapper around the bundled ``register-agent.sh`` shell script, exposed as
the ``band-register-agent`` console entry point. Any project that installs the
SDK can register a Band external agent from a user API key without vendoring the
script — sharing collapses to installing the SDK and running one command.

The shell script is bundled verbatim and prints eval-able output to stdout::

    BAND_AGENT_ID=<uuid>
    BAND_API_KEY=<agent-key>

Usage::

    # Preferred — key from the environment, kept out of the process list:
    export BAND_USER_API_KEY=...
    eval "$(band-register-agent)"

    # Convenience — pass the user key as an optional positional argument:
    eval "$(band-register-agent <user-api-key>)"

When the key is passed as an argument it is exported into the script's
environment as ``BAND_USER_API_KEY``. Note this makes the key visible in the
process list (``ps``); prefer the environment variable on shared machines.

Other knobs are read by the bundled script from the environment: ``BAND_BASE_URL``,
``BAND_AGENT_NAME``, ``BAND_AGENT_DESCRIPTION``.

Exit code mirrors the shell script (0 success, non-zero failure); 127 if ``bash``
is not available.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from importlib.resources import as_file, files

logger = logging.getLogger(__name__)

SCRIPT_NAME = "register-agent.sh"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="band-register-agent",
        description=(
            "Register a Band external agent from a user API key "
            "(wraps the bundled register-agent.sh)."
        ),
        epilog=(
            "Output is eval-able: BAND_AGENT_ID=... and BAND_API_KEY=... are "
            'printed to stdout, e.g. eval "$(band-register-agent)".'
        ),
    )
    parser.add_argument(
        "api_key",
        nargs="?",
        help=(
            "Band user API key with agent-create scope. If omitted, the script "
            "reads BAND_USER_API_KEY from the environment (preferred). Passing "
            "the key here exposes it in the process list (ps); use the env var "
            "on shared machines."
        ),
    )
    return parser


def main() -> None:
    """CLI entry point: run the bundled registration script."""
    args = build_parser().parse_args()

    env = os.environ.copy()
    if args.api_key:
        env["BAND_USER_API_KEY"] = args.api_key

    script = files("band.cli").joinpath(SCRIPT_NAME)
    try:
        with as_file(script) as script_path:
            logger.debug("Running bundled registration script: %s", script_path)
            completed = subprocess.run(
                ["bash", str(script_path)],
                env=env,
                check=False,
            )
    except FileNotFoundError:
        # bash is not on PATH.
        sys.stderr.write(
            "Error: 'bash' is required to run band-register-agent but was not "
            "found on PATH.\n"
        )
        sys.exit(127)

    sys.exit(completed.returncode)


if __name__ == "__main__":
    main()
