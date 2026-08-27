"""Self-registration proof: `band-kit provision` mints a fresh Band agent on
the host — no pre-provisioned identity — and wires its key into a
proxy-managed sandbox it also creates, extending
test_kit_proxy_managed_live.py's never-in-VM proof with a registration
prefix. INT-982's headline acceptance: "with only a user key, a fresh agent
registers and starts; re-provisioning never duplicates it."

`band.docker.provision.run()` is called in-process (not the packaged
`band-kit` console script) so the test exercises the exact orchestration
under test without requiring the script installed in the test venv;
`Sandbox.create` (the sibling test's own VM lifecycle helper) still owns
create/exec/rm, so this doesn't duplicate that teardown-safety logic. The
never-in-VM proofs themselves (injection path, key absence) are already
covered by test_kit_proxy_managed_live.py and are not repeated here — this
module's job is the registration step in front of them.

Runs only on a Docker-Sandbox-capable host with the `sbx` CLI, against the
Band deployment `.env.test` points at. Gated behind BOTH ``sandbox``
(SANDBOX_TESTS_ENABLED=true) and ``e2e`` (E2E_TESTS_ENABLED=true).
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from band.docker.provision import read_agent_id
from band.docker.provision import run as provision_run
from tests.docker.test_kit_proxy_managed_live import (
    _deployment_hosts,
    _prepare_workspace,
)
from tests.docker.toolkit.sbx_cli import (
    CREATE_TIMEOUT_S,
    Sandbox,
    allow_network_for_hosts,
    remove_custom_secret_command,
    sandbox_name,
    sbx_available,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.paths import KIT_DIR
from tests.toolkit.timeouts import backstop_timeout

logger = logging.getLogger(__name__)

_SANDBOX_TIMEOUT = CREATE_TIMEOUT_S + backstop_timeout(
    BaselineSettings().e2e_timeout, extra_s=90
)

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.e2e,
    pytest.mark.timeout(_SANDBOX_TIMEOUT),
]


@pytest.fixture
async def self_registered_sandbox(
    tmp_path: Path,
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
) -> AsyncIterator[tuple[str, str, argparse.Namespace]]:
    """A live agent registered by `provision.run()` itself, running inside a
    proxy-managed sandbox `provision.run()` also injected the secret for.

    Yields ``(agent_id, room_id, args)`` — ``args`` lets the test re-invoke
    `provision.run()` with the identical configuration to prove the
    idempotency guard, without spinning up a second sandbox.
    """
    if not sbx_available():
        pytest.skip("sbx CLI not on PATH")
    user_key = baseline_settings.credentials.api_key_user
    if not user_key:
        pytest.skip("BAND_API_KEY_USER is required")

    workspace = _prepare_workspace(tmp_path, endpoints=baseline_settings.endpoints)
    name = sandbox_name(prefix="band-selfreg")
    args = argparse.Namespace(
        name=name,
        agent_name=None,
        description="Self-registration live E2E proof agent.",
        workspace=workspace,
        host="**.band.ai",
        api_key=user_key,
        rest_url=baseline_settings.endpoints.rest_url,
        create=False,  # Sandbox.create (below) owns the VM lifecycle instead.
        kit=None,
        timeout=60,
        verbose=False,
    )

    agent_id = await provision_run(args)
    assert read_agent_id(workspace) == agent_id, (
        "provision wrote a different agent.id than it returned"
    )

    room_id = await resource_manager.provision_room(participants=[agent_id])
    try:
        hosts = _deployment_hosts(baseline_settings.endpoints)
        with allow_network_for_hosts(hosts, kit=KIT_DIR):
            with Sandbox.create(name=name, kit=KIT_DIR, workspace=workspace):
                yield agent_id, room_id, args
    finally:
        # Not tracked via resource_manager.provision_agent (provision.run()
        # registered it directly with the user key), so it needs its own reap.
        await resource_manager.reap_agent(agent_id)
        # Sandbox.create's own teardown only removes the sandbox (`sbx rm`) --
        # the scoped custom secret provision.run() injected outlives that
        # (verified live: a removed sandbox's `sbx secret ls` still lists it),
        # so it needs its own cleanup too, or the real agent key is left
        # sitting in the host's secret store indefinitely.
        removal = subprocess.run(
            remove_custom_secret_command(sandbox=name, host=args.host),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if removal.returncode != 0:
            logger.warning(
                "failed to remove the scoped Band secret for sandbox %s (exit %s); "
                "remove it manually: sbx secret rm %s --host %s -f",
                name,
                removal.returncode,
                name,
                args.host,
            )


@pytest.mark.asyncio(loop_scope="session")
async def test_self_registered_agent_round_trips_with_no_duplicate_on_reprovision(
    self_registered_sandbox: tuple[str, str, argparse.Namespace],
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """An agent with no pre-existing identity — registered entirely by
    `provision.run()` — connects and round-trips a room message from inside
    the sandbox it was also injected into; a repeat `provision.run()` call
    for the same sandbox then registers no second agent.

    The restart leg of INT-982's acceptance ("stop/restart does not
    duplicate registration") is `sbx`'s own host-secret and `band.yaml`
    persistence guarantee, not code this ticket owns, so it isn't
    re-exercised here (see the kit README's Self-registration section); what
    this proves live is provision.py's own idempotency guard — the part
    this ticket is actually responsible for.
    """
    agent_id, room_id, args = self_registered_sandbox

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id, "ping", mention_id=agent_id, mention_name=agent_id
        )
        replies = await capture.wait_for_reply(mid, agent_id)
    replies.assert_contains_any(["echo:"])
    replies.assert_contains_any(["ping"])

    # A real duplicate registration would either 422 (same agent name) or
    # mint a distinct id -- returning the identical id without raising is
    # only possible if the idempotency guard skipped registration entirely.
    second_id = await provision_run(args)
    assert second_id == agent_id
