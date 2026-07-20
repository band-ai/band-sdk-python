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

Status: scaffold. The absence and cert-issuer proofs are complete; the
provisioning fixture (`proxy_managed_sandbox`) has one live-only seam flagged
below — the secret→credential binding — to confirm on first live execution.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.sbx_cli import PROXY_CA_ISSUER, Sandbox, sbx_available
from tests.paths import KIT_DIR

pytestmark = [pytest.mark.sandbox, pytest.mark.e2e]


@pytest.fixture
def proxy_managed_sandbox() -> Iterator[Sandbox]:
    """A running sandbox created from the kit under proxy-managed custody.

    Live-only seam to confirm on first execution: the Band key must be
    provisioned host-side (``sbx secret set-custom --host app.band.ai``) so the
    kit's `credentials[].apiKey.inject[]` has a value to inject. The exact
    secret→credential binding is the one piece `sbx kit validate` can't verify
    (it is lenient), so validate it here against a real sandbox.
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
    """The Band host's cert issuer proves the MITM/injection path is active."""
    issuer = proxy_managed_sandbox.band_host_cert_issuer("app.band.ai")
    assert PROXY_CA_ISSUER in issuer, issuer


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
