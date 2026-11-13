"""Run the kit's echo agent inside a proxy-managed Docker Sandbox.

The sandbox sibling of ``live_agent.py``: a throwaway echo-agent workspace
pointed at a Band deployment, a sandbox whose real Band key is injected
host-side by the sbx proxy (the VM only holds the ``proxy-managed`` sentinel),
and the self-registration flow where ``band.docker.provision.run()`` mints the
agent itself. Every context manager here confirms its cleanup on exit,
including after a failed start.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from band.credentials import PROXY_MANAGED_API_KEY
from band.docker.launcher.config import PLACEHOLDER_AGENT_ID
from band.docker.provision import read_agent_id
from band.docker.provision import run as provision_run
from tests.docker.toolkit.docker_cli import Image
from tests.docker.toolkit.sbx_cli import (
    SBX,
    Sandbox,
    allow_network_for_hosts,
    custom_secret,
    local_workload,
    remove_custom_secret_command,
    sandbox_name,
)
from tests.e2e.baseline.settings import BandEndpoints, BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.paths import KIT_DIR
from tests.toolkit.tasks import off_loop

logger = logging.getLogger(__name__)

# The proxy injects the Band key for every Band host.
BAND_HOST_PATTERN = "**.band.ai"
# The kit declares PyPI in its network policy; an undeclared host is refused.
DECLARED_HOST_URL = "https://pypi.org/simple/"
UNDECLARED_HOST_URL = "https://example.com"


def deployment_hosts(endpoints: BandEndpoints) -> list[str]:
    """The distinct hosts the agent reaches — REST and WS — in order.

    Both must be network-allowed: a deployment that serves the WS upgrade on a
    different host than REST would otherwise have its WS leg blocked while REST
    succeeds. Band uses one host for both today, so this usually collapses to one.
    """
    hosts = [urlsplit(endpoints.rest_url).hostname, urlsplit(endpoints.ws_url).hostname]
    return list(dict.fromkeys(host for host in hosts if host))


def prepare_workspace(
    dest: Path, *, endpoints: BandEndpoints, agent_id: str | None = None
) -> Path:
    """A throwaway copy of the echo-agent workspace pointed at ``endpoints``.

    Copying — never mounting the shipped ``echo-agent`` in place — keeps sbx's
    workspace writes out of the repo. The endpoints (and, when given, a real
    provisioned ``agent_id``) are non-secret and must be inside the VM for the
    launcher to resolve them (``sbx create`` takes no ``--env``); only the *key*
    stays out, injected by the proxy. Without ``agent_id`` the kit's placeholder
    id is left as shipped — the cert/absence proofs don't need a connecting
    identity. Returns the copy's path.
    """
    workspace = dest / "echo-agent"
    shutil.copytree(KIT_DIR / "echo-agent", workspace)
    config_path = workspace / "band.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if agent_id is not None:
        config["agent"]["id"] = agent_id
    config["band"] = {"restUrl": endpoints.rest_url, "wsUrl": endpoints.ws_url}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return workspace


@contextmanager
def proxy_managed_sandbox(
    *,
    workspace: Path,
    band_key: str,
    endpoints: BandEndpoints,
    image: Image,
    attached: bool = True,
) -> Iterator[Sandbox]:
    """A proxy-managed sandbox wired to reach ``endpoints``, torn down on exit.

    Injects ``band_key`` for the Band host **scoped to this one sandbox** — never
    the operator's global credential — and grants every Band host the agent
    reaches that the kit's baseline doesn't already cover (a non-prod
    deployment). Everything is in force before ``sbx create`` (so it holds at
    startup) and removed on exit.
    """
    name = sandbox_name()
    with ExitStack() as stack:
        stack.enter_context(
            allow_network_for_hosts(deployment_hosts(endpoints), kit=KIT_DIR)
        )
        stack.enter_context(
            custom_secret(
                sandbox=name,
                host=BAND_HOST_PATTERN,
                env="BAND_API_KEY",
                value=band_key,
                placeholder=PROXY_MANAGED_API_KEY,
            )
        )
        kit = stack.enter_context(local_workload(image.tag))
        yield stack.enter_context(
            Sandbox.create(name=name, kit=kit, workspace=workspace, attached=attached)
        )


def scoped_secret_present(name: str) -> bool:
    """Whether sandbox ``name`` still has the proxy-managed Band secret."""
    result = subprocess.run(
        [SBX, "secret", "ls", "--sandbox", name, "--json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return any(
        secret["placeholder"] == PROXY_MANAGED_API_KEY
        for secret in json.loads(result.stdout)["custom_secrets"]
    )


def _remove_scoped_secret(name: str) -> None:
    """Remove sandbox ``name``'s Band secret and confirm it is gone."""
    if not scoped_secret_present(name):
        return
    command = remove_custom_secret_command(sandbox=name)
    removal = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=60
    )
    if removal.returncode:
        raise RuntimeError(
            f"Scoped secret cleanup incomplete (exit {removal.returncode}); "
            f"retry: {' '.join(command)}"
        )
    if scoped_secret_present(name):
        raise RuntimeError(f"Scoped secret still present; retry: {' '.join(command)}")


@contextmanager
def _registered_sandbox(
    name: str, workspace: Path, endpoints: BandEndpoints, image: Image
) -> Iterator[Sandbox]:
    """The sandbox ``provision.run()`` already injected the secret for."""
    with (
        allow_network_for_hosts(deployment_hosts(endpoints), kit=KIT_DIR),
        local_workload(image.tag) as workload,
        Sandbox.create(name=name, kit=workload, workspace=workspace) as sandbox,
    ):
        yield sandbox


@asynccontextmanager
async def self_registered_sandbox(
    workdir: Path,
    resource_manager: ResourceManager,
    settings: BaselineSettings,
    image: Image,
) -> AsyncIterator[tuple[str, str, argparse.Namespace]]:
    """A live agent registered by ``provision.run()`` itself, running inside a
    proxy-managed sandbox ``provision.run()`` also injected the secret for.

    Yields ``(agent_id, room_id, args)`` — ``args`` lets the caller re-invoke
    ``provision.run()`` with the identical configuration to prove the
    idempotency guard. Cleanup attempts the agent and the scoped secret
    independently, even when registration fails after persisting the id.
    """
    workspace = prepare_workspace(workdir, endpoints=settings.endpoints)
    name = sandbox_name(prefix="band-selfreg")
    args = argparse.Namespace(
        name=name,
        agent_name=None,
        description="Self-registration live E2E proof agent.",
        workspace=workspace,
        host=BAND_HOST_PATTERN,
        api_key=settings.credentials.api_key_user,
        rest_url=settings.endpoints.rest_url,
        create=False,  # Sandbox.create (below) owns the VM lifecycle instead.
        kit=None,
        timeout=60,
        verbose=False,
    )

    agent_id: str | None = None
    try:
        agent_id = await provision_run(args)
        assert read_agent_id(workspace) == agent_id, (
            "provision wrote a different agent.id than it returned"
        )
        room_id = await resource_manager.provision_room(participants=[agent_id])
        sandbox = _registered_sandbox(name, workspace, settings.endpoints, image)
        async with off_loop(sandbox):
            yield agent_id, room_id, args
    finally:
        failures: list[Exception] = []
        # The CLI may persist the id before secret injection or creation fails.
        known_agent_id = agent_id
        if known_agent_id is None:
            try:
                known_agent_id = read_agent_id(workspace)
            except Exception as error:
                logger.exception("Could not recover agent id for sandbox %s", name)
                error.add_note(f"Could not recover agent id for sandbox {name}")
                failures.append(error)
        if known_agent_id and known_agent_id != PLACEHOLDER_AGENT_ID:
            try:
                await resource_manager.reap_agent(known_agent_id)
            except Exception as error:
                logger.exception("Could not clean up agent %s", known_agent_id)
                error.add_note(f"Cleanup incomplete for agent {known_agent_id}")
                failures.append(error)
        try:
            _remove_scoped_secret(name)
        except Exception as error:
            logger.exception(
                "Could not confirm scoped secret cleanup for sandbox %s", name
            )
            error.add_note(f"Cleanup incomplete for scoped secret in sandbox {name}")
            failures.append(error)
        if failures:
            raise ExceptionGroup("Self-registration cleanup incomplete", failures)


def workload_posture(sandbox: Sandbox) -> tuple[str, str, str]:
    """``(uid, declared-host status, undeclared-host status)`` seen inside the VM."""

    def status(url: str) -> str:
        return sandbox.exec(f"curl -sS -o /dev/null -w '%{{http_code}}' {url}")

    return (
        sandbox.exec("id -u"),
        status(DECLARED_HOST_URL),
        status(UNDECLARED_HOST_URL),
    )
