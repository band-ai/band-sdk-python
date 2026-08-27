"""Self-registration demo: `band-kit provision`, end to end.

Demonstrates the self-registration flow with no pre-provisioned Band
agent: registers a fresh agent on the host with only a user key, boots it
into a real Docker Sandbox, sends it a message, checks the echo reply, then
tears every provisioned resource back down.

Like ``examples/sandbox/staging-smoke/probe.py``, this is not a regular
customer-facing example: it drives a real ``sbx`` sandbox and needs a
nested-virtualization-capable host, so it runs from a repo checkout against
this repo's own dev venv (reusing the E2E baseline toolkit for room/message
plumbing) rather than as a standalone PEP 723 script. Not run in CI.

Prerequisites:
    - Docker Sandboxes (`sbx`) installed and signed in (`sbx login`)
    - The band-python-kit image available to the sandbox runtime: either a
      published tag, or built + loaded locally (see the kit README's
      "Developing the kit" section)
    - `.env.test` at the repo root with BAND_API_KEY_USER (+ BAND_REST_URL /
      BAND_WS_URL for a non-production deployment) — same convention every
      other E2E/live tool in this repo uses

Run with (from the repo root, dev venv):
    uv run examples/sandbox/self-registration/demo.py
    uv run examples/sandbox/self-registration/demo.py --kit /path/to/docker/band_python_kit

Cleanup runs automatically (success or failure) via try/finally. If the
process is killed outright (SIGKILL, host crash) that finally block never
runs, leaving an orphaned sandbox/secret/agent behind under the random name
printed in the "Run name" log line -- recover with:
    uv run examples/sandbox/self-registration/demo.py --cleanup <that-name>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

# tests.* is dev-only source, not part of the published band-sdk package, so
# this (like examples/sandbox/staging-smoke/probe.py) runs from a repo
# checkout and needs the repo root on sys.path -- fixed by this file's own
# location (examples/sandbox/self-registration/), not a generic walk-up.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from band import LogSettings, LogStream
from band.docker.provision import read_agent_id
from tests.docker.test_kit_proxy_managed_live import (
    _deployment_hosts,
    _prepare_workspace,
)
from tests.docker.toolkit.sbx_cli import (
    allow_network_for_hosts,
    remove_custom_secret_command,
    sandbox_name,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import Replies, reply_capture
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, user_rest_client
from tests.e2e.baseline.toolkit.ws import user_ws_observer
from tests.paths import KIT_DIR

logger = logging.getLogger(__name__)

AGENT_DESCRIPTION = "examples/sandbox/self-registration demo agent."
BAND_HOST_PATTERN = "**.band.ai"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kit",
        default=str(KIT_DIR),
        help=f"Kit reference for `sbx create --kit` (default: local checkout, {KIT_DIR})",
    )
    parser.add_argument(
        "--cleanup",
        metavar="NAME",
        default=None,
        help=(
            "Skip the demo; just tear down a prior run's sandbox/secret/agent by "
            "the name it printed (recovers from an interrupted run's finally "
            "block never having run)."
        ),
    )
    parser.add_argument("--verbose", action="store_true", default=False)
    return parser


def _require_settings() -> BaselineSettings:
    settings = BaselineSettings()
    if not settings.credentials.api_key_user:
        raise SystemExit("BAND_API_KEY_USER is required (see .env.test)")
    return settings


def _band_kit_provision(
    *,
    name: str,
    workspace: Path,
    agent_name: str | None = None,
    kit: str | None = None,
    create: bool,
) -> str:
    """Run the real installed `band-kit provision` CLI; return the agent id.

    Shells out rather than calling `band.docker.provision.run()` directly, so
    this demo proves the CLI a customer actually types, not just its internals.
    """
    argv = [
        "band-kit",
        "provision",
        "--name",
        name,
        "--description",
        AGENT_DESCRIPTION,
        "--workspace",
        str(workspace),
    ]
    if agent_name:
        argv += ["--agent-name", agent_name]
    if create:
        assert kit, "kit is required when create=True"
        argv += ["--create", "--kit", kit]
    else:
        argv += ["--no-create"]

    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"band-kit provision failed: {result.stderr.strip()}")
    return result.stdout.strip()


async def _ping_and_await_echo(
    resource_manager: ResourceManager,
    *,
    settings: BaselineSettings,
    room_id: str,
    agent_id: str,
    agent_name: str,
) -> Replies:
    """Send a mention and wait for the agent's echo reply, subscribed first."""
    async with (
        user_ws_observer(settings) as tracking,
        reply_capture(
            tracking,
            room_id,
            user_ops=resource_manager.user_ops,
            settings=settings,
            deadline_s=settings.e2e_timeout,
        ) as capture,
    ):
        mid = await resource_manager.user_ops.send_message(
            room_id, "ping", mention_id=agent_id, mention_name=agent_name
        )
        return await capture.wait_for_reply(mid, agent_id)


def _teardown_sbx(name: str, *, host: str = BAND_HOST_PATTERN) -> None:
    """`sbx rm` + `sbx secret rm` for `name` -- shared by the happy-path
    finally block and standalone `--cleanup`, so the two can't drift."""
    subprocess.run(
        ["sbx", "rm", "-f", name], capture_output=True, text=True, check=False
    )
    subprocess.run(
        remove_custom_secret_command(sandbox=name, host=host),
        capture_output=True,
        text=True,
        check=False,
    )


async def cleanup_by_name(name: str) -> None:
    """Standalone recovery for a run whose finally block never got to run
    (e.g. the process was killed). Removes the sandbox and its scoped secret,
    and any agent registered under this run's display name.

    Cannot recover the room: unlike the agent, it carries no name this demo
    set, so there is nothing to search by. It's a harmless orphan (no plan
    cap, no ongoing cost) -- delete it by hand from the Band UI if it matters.
    """
    settings = _require_settings()
    client = user_rest_client(settings)
    resource_manager = ResourceManager(
        user_client=client, settings=settings, run_id=name
    )

    agents = await client.human_api_agents.list_my_agents(page=1, page_size=100)
    matches = [a for a in (agents.data or []) if name in (a.name or "")]
    for agent in matches:
        logger.info("Deleting orphaned agent %s (%s)", agent.id, agent.name)
        await resource_manager.reap_agent(agent.id)
    if not matches:
        logger.info("No registered agent found matching %r", name)

    _teardown_sbx(name)
    logger.info(
        "Cleanup complete for %s: sandbox, secret, and any matching agent removed", name
    )


async def run(kit: str) -> None:
    settings = _require_settings()
    client = user_rest_client(settings)

    name = sandbox_name(prefix="band-selfreg-demo")
    resource_manager = ResourceManager(
        user_client=client, settings=settings, run_id=name
    )
    # Embeds `name` (already unique) rather than a fixed literal: the platform
    # rejects a duplicate agent name (422), so re-running this demo against
    # the same account would collide with a still-registered prior run.
    agent_name = f"Self-registration demo ({name})"
    logger.info("Run name: %s", name)

    room_id: str | None = None
    agent_id: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="band-selfreg-demo-") as tmp_dir:
            workspace = _prepare_workspace(Path(tmp_dir), endpoints=settings.endpoints)
            hosts = _deployment_hosts(settings.endpoints)

            with allow_network_for_hosts(hosts, kit=kit):
                logger.info("Step 1/4: band-kit provision --create (registers + boots)")
                agent_id = _band_kit_provision(
                    name=name,
                    workspace=workspace,
                    agent_name=agent_name,
                    kit=kit,
                    create=True,
                )
                assert read_agent_id(workspace) == agent_id
                logger.info("Registered and booted agent: %s", agent_id)

                logger.info("Step 2/4: creating room and adding the agent")
                room_id = await resource_manager.provision_room(participants=[agent_id])

                logger.info(
                    "Step 3/4: sending a room message and awaiting the echo reply"
                )
                replies = await _ping_and_await_echo(
                    resource_manager,
                    settings=settings,
                    room_id=room_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                )
                replies.assert_contains_any(["echo:"])
                logger.info("Got a reply containing 'echo:'")

                logger.info("Step 4/4: re-running provision to prove idempotency")
                rerun_id = _band_kit_provision(
                    name=name, workspace=workspace, create=False
                )
                if rerun_id != agent_id:
                    raise RuntimeError(
                        f"expected the idempotent no-op to return {agent_id!r}, got {rerun_id!r}"
                    )
                logger.info("Confirmed idempotent: no duplicate agent registered")

        print(f"\nSuccess: agent {agent_id} self-registered and round-tripped.\n")
    finally:
        logger.info("Cleaning up...")
        _teardown_sbx(name)
        if room_id:
            await resource_manager.reap_room(room_id)
        if agent_id:
            await resource_manager.reap_agent(agent_id)
        logger.info("Cleanup complete: sandbox, secret, room, and agent all removed")


def main() -> None:
    args = build_parser().parse_args()
    settings = (
        LogSettings(log_level="DEBUG", log_stream=LogStream.STDERR)
        if args.verbose
        else LogSettings(log_stream=LogStream.STDERR)
    )
    # This script's own logger is __main__, not band.* -- for_application()
    # raises it (and root) to the same level so its INFO lines aren't silent.
    settings.for_application().configure()
    try:
        asyncio.run(cleanup_by_name(args.cleanup) if args.cleanup else run(args.kit))
    except (RuntimeError, SystemExit) as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
