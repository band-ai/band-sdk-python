"""Repository validation tool for self-registration with a v3 sbx workload.

Run from the repository root with its dev environment and canonical .env.test:
    uv run examples/sandbox/self-registration/demo.py --kit band-python-kit:local

The owned PTY starts the foreground workload command. Known resource ids are
persisted under .runs before later steps can fail; interrupted runs recover via
    uv run examples/sandbox/self-registration/demo.py --cleanup <run-name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

from band_rest.core.api_error import ApiError
from dotenv import dotenv_values, load_dotenv
from httpx import HTTPStatusError

# tests.* is dev-only source, not part of the published band-sdk package, so
# this (like examples/sandbox/staging-smoke/probe.py) runs from a repo
# checkout and needs the repo root on sys.path -- fixed by this file's own
# location (examples/sandbox/self-registration/), not a generic walk-up.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic import ValidationError

from band import LogSettings, LogStream
from band.docker.provision import DEFAULT_BAND_HOST, read_agent_id
from tests.docker.test_kit_proxy_managed_live import (
    _deployment_hosts,
    _prepare_workspace,
)
from tests.docker.toolkit.sbx_cli import (
    allow_network_for_hosts,
    attached_run,
    local_workload,
    remove_custom_secret_command,
    sandbox_name,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import Replies, reply_capture
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, user_rest_client
from tests.e2e.baseline.toolkit.ws import user_ws_observer
from tests.paths import ENV_TEST_FILE, EXAMPLES_ROOT, KIT_DIR

logger = logging.getLogger(__name__)

AGENT_DESCRIPTION = "examples/sandbox/self-registration demo agent."
BAND_HOST_PATTERN = DEFAULT_BAND_HOST
LEDGER_DIR = EXAMPLES_ROOT / "sandbox" / "self-registration" / ".runs"
DEFAULT_WORKLOAD = "band-python-kit:local"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kit",
        default=DEFAULT_WORKLOAD,
        help=f"v3 workload reference (default: {DEFAULT_WORKLOAD})",
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
    load_dotenv(ENV_TEST_FILE, override=True)
    settings = BaselineSettings()
    values = dotenv_values(ENV_TEST_FILE)
    settings.credentials = settings.credentials.model_copy(
        update={"api_key_user": values.get("BAND_API_KEY_USER") or ""}
    )
    settings.endpoints = settings.endpoints.model_copy(
        update={
            "rest_url": values.get("BAND_REST_URL")
            or type(settings.endpoints).model_fields["rest_url"].default,
            "ws_url": values.get("BAND_WS_URL")
            or type(settings.endpoints).model_fields["ws_url"].default,
        }
    )
    if not settings.credentials.api_key_user:
        raise ValueError("BAND_API_KEY_USER is required (see .env.test)")
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
        "--rest-url",
        _require_settings().endpoints.rest_url,
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


def ledger_path(name: str) -> Path:
    if not re.fullmatch(r"band-selfreg-demo-[A-Za-z0-9-]+", name):
        raise ValueError("Cleanup requires the printed band-selfreg-demo run name")
    return LEDGER_DIR / name


def record_resource(name: str, kind: str, resource_id: str) -> None:
    ledger = ledger_path(name)
    ledger.mkdir(parents=True, exist_ok=True)
    with (ledger / kind).open("a", encoding="utf-8") as records:
        records.write(f"{resource_id}\n")


def recorded_resources(name: str, kind: str) -> list[str]:
    path = ledger_path(name) / kind
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _teardown_sbx(name: str, *, host: str = BAND_HOST_PATTERN) -> bool:
    failed = False
    probes = (
        (["sbx", "ls", "-q"], ["sbx", "rm", "-f", name]),
        (
            ["sbx", "secret", "ls", "--sandbox", name, "--json"],
            remove_custom_secret_command(sandbox=name),
        ),
    )
    for probe, removal in probes:
        resource = "sandbox" if probe[1] == "ls" else "scoped secret"
        try:
            result = subprocess.run(
                probe, capture_output=True, text=True, check=False, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            logger.exception("Could not inspect cleanup state: %s", " ".join(probe))
            failed = True
            continue
        if result.returncode:
            logger.error("Could not inspect cleanup state: %s", " ".join(probe))
            failed = True
            continue
        if probe[1] == "ls":
            present = name in result.stdout.splitlines()
        else:
            try:
                secrets = json.loads(result.stdout)["custom_secrets"]
                present = any(host in secret["targets"] for secret in secrets)
            except (ValueError, KeyError, TypeError):
                logger.exception("Invalid secret listing; retaining recovery ledger")
                failed = True
                continue
        if present:
            try:
                removed = subprocess.run(
                    removal, capture_output=True, text=True, check=False, timeout=60
                )
            except (OSError, subprocess.SubprocessError):
                logger.exception("Could not remove %s for run %s", resource, name)
                failed = True
                continue
            if removed.returncode:
                logger.error(
                    "Could not remove %s for run %s (exit %s)",
                    resource,
                    name,
                    removed.returncode,
                )
                failed = True
    return not failed


async def cleanup_by_name(name: str) -> None:
    record_resource(name, "sandbox", name)
    try:
        failed = not _teardown_sbx(name)
    except (OSError, subprocess.SubprocessError):
        logger.exception("Sandbox cleanup unavailable; continuing platform cleanup")
        failed = True
    try:
        settings = _require_settings()
    except ValueError as error:
        raise RuntimeError(
            f"Platform cleanup blocked; ledger retained at {ledger_path(name)}. Retry --cleanup {name} after restoring .env.test"
        ) from error
    client = user_rest_client(settings)
    resource_manager = ResourceManager(
        user_client=client, settings=settings, run_id=name
    )
    agent_ids = recorded_resources(name, "agents")
    registration_known = (ledger_path(name) / "agents").exists()
    if not registration_known:
        try:
            response = await client.human_api_agents.list_my_agents(
                name=f"Self-registration demo ({name})", page=1, page_size=100
            )
        except ValidationError:
            logger.exception("Invalid agent listing; continuing sandbox cleanup")
            failed = True
        except Exception:
            logger.exception("Could not list agents; continuing sandbox cleanup")
            failed = True
        else:
            registration_known = True
            for agent in response.data or []:
                if agent.name == f"Self-registration demo ({name})":
                    record_resource(name, "agents", agent.id)
                    agent_ids.append(agent.id)
    for kind, ids, reap in (
        ("rooms", recorded_resources(name, "rooms"), resource_manager.reap_room),
        ("agents", agent_ids, resource_manager.reap_agent),
    ):
        unresolved: list[str] = []
        for resource_id in ids:
            try:
                await reap(resource_id)
            except (ApiError, HTTPStatusError) as error:
                status = (
                    error.status_code
                    if isinstance(error, ApiError)
                    else error.response.status_code
                )
                if status != 404:
                    logger.exception("Could not remove %s %s", kind, resource_id)
                    unresolved.append(resource_id)
            except ValidationError:
                logger.exception(
                    "Invalid cleanup response for %s %s", kind, resource_id
                )
                unresolved.append(resource_id)
            except Exception:
                logger.exception("Could not remove %s %s", kind, resource_id)
                unresolved.append(resource_id)
        path = ledger_path(name) / kind
        if kind == "agents" and not registration_known:
            continue
        if unresolved:
            path.write_text("\n".join(unresolved) + "\n", encoding="utf-8")
            failed = True
        else:
            path.write_text("", encoding="utf-8")
    if failed:
        raise RuntimeError(
            f"Cleanup incomplete; ledger retained at {ledger_path(name)}. Retry: uv run examples/sandbox/self-registration/demo.py --cleanup {name}"
        )
    ledger = ledger_path(name)
    if ledger.exists():
        for record in ledger.iterdir():
            record.unlink()
        ledger.rmdir()
    logger.info(
        "Cleanup complete: sandbox, scoped secret and recorded platform resources removed"
    )


async def run(kit: str) -> None:
    settings = _require_settings()
    client = user_rest_client(settings)

    name = sandbox_name(prefix="band-selfreg-demo")
    record_resource(name, "sandbox", name)
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
        workload_context = (
            local_workload(kit) if kit == DEFAULT_WORKLOAD else nullcontext(kit)
        )
        with (
            tempfile.TemporaryDirectory(prefix="band-selfreg-demo-") as tmp_dir,
            workload_context as workload,
        ):
            workspace = _prepare_workspace(Path(tmp_dir), endpoints=settings.endpoints)
            hosts = _deployment_hosts(settings.endpoints)

            with allow_network_for_hosts(hosts, kit=KIT_DIR):
                logger.info(
                    "Step 1/5: band-kit provision --create (registers + creates the sandbox)"
                )
                agent_id = _band_kit_provision(
                    name=name,
                    workspace=workspace,
                    agent_name=agent_name,
                    kit=str(workload),
                    create=True,
                )
                record_resource(name, "agents", agent_id)
                assert read_agent_id(workspace) == agent_id
                logger.info("Registered agent: %s", agent_id)

                logger.info(
                    "Step 2/5: attaching to launch the agent "
                    "(the workload command runs under its owned PTY)"
                )
                with attached_run(name):
                    logger.info("Step 3/5: creating room and adding the agent")
                    room_id = await resource_manager.provision_room(
                        title=f"Self-registration demo ({name})"
                    )
                    record_resource(name, "rooms", room_id)
                    await resource_manager.user_ops.add_participant(room_id, agent_id)

                    logger.info(
                        "Step 4/5: sending a room message and awaiting the echo reply"
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

                    logger.info("Step 5/5: re-running provision to prove idempotency")
                    rerun_id = _band_kit_provision(
                        name=name, workspace=workspace, create=False
                    )
                    if rerun_id != agent_id:
                        raise RuntimeError(
                            f"expected the idempotent no-op to return {agent_id!r}, got {rerun_id!r}"
                        )
                    logger.info("Confirmed idempotent: no duplicate agent registered")

        logger.info("Success: agent %s self-registered and round-tripped.", agent_id)
    finally:
        logger.info("Cleaning up...")
        await cleanup_by_name(name)


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
    except (RuntimeError, ValueError, SystemExit) as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
