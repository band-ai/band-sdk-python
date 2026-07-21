"""Never-in-VM proof: proxy-managed custody keeps the real Band key off the sandbox.

Under proxy-managed custody (`sbx create --kit` + host-side `sbx secret
set-custom`), with only the sentinel in the VM, this checks:

1. **injection path** — the Band host's cert, *through the sandbox proxy*, is the
   Docker Sandboxes MITM cert (the request rides the injection path, not a plain
   tunnel). Note: this proves the path is active, **not** that the correct key
   authenticates — that is (3).
2. **never in VM** — the exact key the fixture provisioned into `set-custom` is
   absent from the VM's env, files, and the checked `/proc` entries.
3. **correct injection (auth)** — the agent connects (REST + WS) and round-trips
   a room message, i.e. the proxy replaced the sentinel with a *working* key.
   This is the only check that proves authentication; it provisions a live agent
   whose key is injected on the wire and never enters the VM.

Runs only on a Docker-Sandbox-capable host (nested virtualization) with the
`sbx` CLI, against the Band deployment `.env.test` points at. That deployment's
host must be reachable from the sandbox — the kit allowlists `app.band.ai`, so a
non-prod target also needs `sbx policy allow network`. Gated behind BOTH
``sandbox`` (SANDBOX_TESTS_ENABLED=true) and ``e2e`` (E2E_TESTS_ENABLED=true);
skipped on every ordinary/CI run.

Status: all three checks are implemented against a self-provisioned secret;
running them live against staging is the remaining step.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.sbx_cli import (
    CREATE_TIMEOUT_S,
    PROXY_CA_MARKER,
    Sandbox,
    custom_secret,
    sbx_available,
)
from tests.e2e.baseline.settings import BaselineSettings, BandEndpoints
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.paths import KIT_DIR
from tests.toolkit.timeouts import backstop_timeout

# `sbx create` boots a microVM (up to CREATE_TIMEOUT_S) and the round-trip then
# waits a live-turn budget on top; the global 30s ini timeout would kill either,
# so every sandbox test in this module gets this ceiling.
_SANDBOX_TIMEOUT = CREATE_TIMEOUT_S + backstop_timeout(
    BaselineSettings().e2e_timeout, extra_s=90
)

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.e2e,
    pytest.mark.timeout(_SANDBOX_TIMEOUT),
]


@pytest.fixture
def band_host() -> str:
    """The Band host the agent is pointed at (from settings) — the injection and
    absence checks both target exactly this host."""
    return urlsplit(BaselineSettings().endpoints.rest_url).hostname or "app.band.ai"


@pytest.fixture
def provisioned_sandbox(band_host: str) -> Iterator[tuple[Sandbox, str]]:
    """A sandbox under proxy-managed custody, plus the real key it was given.

    The fixture provisions the `set-custom` secret **itself** with the real Band
    key, so the value the absence check asserts-absent is exactly the one the
    proxy would inject — no guessing which key is stored. The kit bakes no
    `credentials` block (the kit-declared form crashes `sbx create` 0.35.0);
    injection is host-side `set-custom`, verified live to flip the Band host's
    TLS to the Docker Sandboxes MITM cert. Yields ``(sandbox, real_key)``.
    """
    if not sbx_available():
        pytest.skip("sbx CLI not on PATH")
    real_key = BaselineSettings().credentials.api_key_user
    if not real_key or real_key == PROXY_MANAGED_API_KEY:
        pytest.skip("a real Band key (BAND_API_KEY_USER) is required")

    workspace = KIT_DIR / "echo-agent"  # defaults to proxy-managed custody
    with custom_secret(
        host="**.band.ai",
        env="BAND_API_KEY",
        value=real_key,
        placeholder=PROXY_MANAGED_API_KEY,
    ):
        with Sandbox.create(kit=KIT_DIR, workspace=workspace) as sandbox:
            yield sandbox, real_key


def test_band_host_rides_the_injection_path(
    provisioned_sandbox: tuple[Sandbox, str], band_host: str
) -> None:
    """The Band host's cert (through the proxy) is the injection MITM cert.

    Proves the injection *path* is active for this host — not, on its own, that
    the injected key authenticates (see the round-trip test)."""
    sandbox, _ = provisioned_sandbox
    cert = sandbox.band_host_cert(band_host)
    # Control: the probe actually produced a cert (didn't error / return empty),
    # so a missing marker means "wrong issuer", not "we never looked".
    assert "cert-probe-error" not in cert, cert
    assert "issuer:" in cert, cert
    assert PROXY_CA_MARKER in cert, cert


def test_real_band_key_never_enters_the_vm(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    """The provisioned real key is absent from the VM; only the sentinel is present.

    The asserted-absent value is the one the fixture stored in `set-custom`, so a
    pass means *that* key never reached the VM. The scan is targeted (env,
    ``/proc``, common writable paths), not an exhaustive filesystem sweep."""
    sandbox, real_key = provisioned_sandbox
    environ = sandbox.all_process_environ()

    # Positive control FIRST: prove we actually captured the agent's live
    # environment (its BAND_API_KEY is exactly the sentinel, on its own
    # VAR=value line). Without this, every absence check below could pass
    # vacuously over empty output (agent down, /proc denied, tool missing).
    assert f"BAND_API_KEY={PROXY_MANAGED_API_KEY}" in environ, "agent env not observed"

    # Absence is now meaningful: the real key is in no process env, the exec
    # shell's env, or any searched file.
    assert real_key not in environ
    assert real_key not in sandbox.shell_env()
    assert (
        sandbox.files_containing(
            real_key, search_paths=["/home/agent", "/etc", "/tmp", "/workspace"]
        )
        == ""
    )


def _round_trip_workspace(
    dest: Path, *, agent_id: str, endpoints: BandEndpoints
) -> Path:
    """A throwaway copy of the echo-agent workspace with the provisioned identity
    baked in.

    The agent id and endpoints are non-secret and must be inside the VM for the
    launcher to resolve them (``sbx create`` takes no ``--env``); only the *key*
    stays out, injected by the proxy. Returns the copy's path.
    """
    workspace = dest / "echo-agent"
    shutil.copytree(KIT_DIR / "echo-agent", workspace)
    config_path = workspace / "band.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["agent"]["id"] = agent_id
    config["band"] = {"restUrl": endpoints.rest_url, "wsUrl": endpoints.ws_url}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return workspace


@pytest.fixture
async def round_trip_sandbox(
    tmp_path: Path,
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
) -> AsyncIterator[tuple[ProvisionedAgent, str]]:
    """A live echo agent inside a proxy-managed sandbox, plus its room.

    Provisions a real agent, bakes its id + the settings endpoints into a
    throwaway workspace, and injects its *key* host-side with the same
    ``set-custom`` placeholder the kit documents — so the VM holds only the
    sentinel. The kit's startup command launches the agent at ``sbx create`` and
    the proxy swaps the sentinel for the real key on the wire. Yields
    ``(agent, room_id)`` to drive one turn against.
    """
    if not sbx_available():
        pytest.skip("sbx CLI not on PATH")

    agent = await resource_manager.provision_agent("nevervm-roundtrip")
    room_id = await resource_manager.provision_room(participants=[agent.id])
    workspace = _round_trip_workspace(
        tmp_path, agent_id=agent.id, endpoints=baseline_settings.endpoints
    )
    with custom_secret(
        host="**.band.ai",
        env="BAND_API_KEY",
        value=agent.api_key,
        placeholder=PROXY_MANAGED_API_KEY,
    ):
        with Sandbox.create(kit=KIT_DIR, workspace=workspace):
            yield agent, room_id


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_connects_and_round_trips_under_injection(
    round_trip_sandbox: tuple[ProvisionedAgent, str],
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The agent connects (REST + WS) and round-trips a room message — the only
    check that proves the proxy injected a *working* key (authentication), not
    just that the injection path is present.

    With only the sentinel in its VM env, a reply can come back solely because
    the proxy replaced that sentinel with a real, valid key on both legs — REST
    (``X-API-Key``) and the WS upgrade (the platform prefers the injected
    ``x-api-key`` header over the sentinel-carrying query).
    """
    agent, room_id = round_trip_sandbox

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id, "ping", mention_id=agent.id, mention_name=agent.name
        )
        replies = await capture.wait_for_reply(mid, agent.id)

    replies.assert_contains_any(["echo:"])
    replies.assert_contains_any(["ping"])
