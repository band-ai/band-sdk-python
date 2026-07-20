"""Never-in-VM proof: proxy-managed custody keeps the real keys off the sandbox.

The ticket's central acceptance criterion, scripted. Under proxy-managed custody
(`sbx create --kit`), with only sentinels in the VM, this proves:

1. the agent connects to Band (REST + WS) and round-trips a room message;
2. the real Band key is absent from the VM's env, files, and ``/proc``;
3. the Band host's TLS cert issuer is the "Docker Sandboxes Proxy CA" — i.e. the
   request rode the credential-injection (MITM) path, not a plain tunnel.

Runs only on a Docker-Sandbox-capable host (nested virtualization) with the
`sbx` CLI, against a staging Band, and needs the Band key provisioned host-side:

    sbx secret set-custom -g --host app.band.ai \\
        --env BAND_API_KEY --placeholder proxy-managed --value <staging-band-key>

Gated behind BOTH ``sandbox`` (SANDBOX_TESTS_ENABLED=true) and ``e2e``
(E2E_TESTS_ENABLED=true); skipped on every ordinary/CI run.

Status: scaffold. The wildcard `**.band.ai` injection is verified live (the Band
host presents an "O=Docker Sandboxes" MITM cert through the proxy). The absence
and cert-path proofs are wired; the REST+WS room round-trip is flagged for first
live execution.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.sbx_cli import PROXY_CA_MARKER, Sandbox, sbx_available
from tests.paths import KIT_DIR

pytestmark = [pytest.mark.sandbox, pytest.mark.e2e]


@pytest.fixture
def proxy_managed_sandbox() -> Iterator[Sandbox]:
    """A running sandbox created from the kit under proxy-managed custody.

    Prerequisite (host-side, per deployment — this is the controllable knob):
    provision the Band key for the target host before running, e.g.::

        sbx secret set-custom -g --host '**.band.ai' \\
            --env BAND_API_KEY --placeholder proxy-managed --value <band-key>

    The kit does not bake a `credentials` block (the kit-declared form crashes
    `sbx create` on 0.35.0); injection is host-side `set-custom`, verified live to
    flip the Band host's TLS to the Docker Sandboxes MITM cert.
    """
    if not sbx_available():
        pytest.skip("sbx CLI not on PATH")
    # The echo-agent starter is the workspace; it defaults to proxy-managed.
    workspace = KIT_DIR / "echo-agent"
    with Sandbox.create(kit=KIT_DIR, workspace=workspace) as sandbox:
        yield sandbox


def test_real_band_key_never_enters_the_vm(
    proxy_managed_sandbox: Sandbox,
    # baseline settings supply the real staging key to assert-absent (never logged)
) -> None:
    """The real Band key is nowhere in the VM — env, files, or /proc."""
    real_key = _staging_band_key()
    assert real_key != PROXY_MANAGED_API_KEY, (
        "test must use the real key, not the sentinel"
    )
    assert proxy_managed_sandbox.real_secret_absent(
        real_key, search_paths=["/home/agent", "/etc", "/tmp"]
    )
    # The sentinel is what the VM actually holds in the agent's environment.
    assert PROXY_MANAGED_API_KEY in proxy_managed_sandbox.process_environ(1)


def test_band_host_rides_the_injection_path(proxy_managed_sandbox: Sandbox) -> None:
    """The Band host's cert (through the proxy) proves the injection path is active."""
    from tests.e2e.baseline.settings import BaselineSettings

    host = urlsplit(BaselineSettings().rest_url).hostname or "app.band.ai"
    cert = proxy_managed_sandbox.band_host_cert(host)
    assert PROXY_CA_MARKER in cert, cert


@pytest.mark.skip(
    reason="scaffold: wire agent provisioning + reply capture on first live run"
)
def test_agent_connects_and_round_trips_under_injection() -> None:
    """The agent connects over REST + WS and round-trips a room message.

    Reuse the baseline toolkit (`resource_manager` to provision the agent,
    `user_ops` to send, `reply_capture` to await the reply) exactly as
    ``test_band_python_kit_live.py`` does — but with the agent running inside
    the `sbx` sandbox (proxy-managed) rather than a bare `docker run`.
    """


def _staging_band_key() -> str:
    """The real staging Band key, from settings — read here so the value is
    never inlined; used only to assert its absence from the VM."""
    from tests.e2e.baseline.settings import BaselineSettings

    return BaselineSettings().api_key_user
